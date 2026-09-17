import base64

EXTRACT_JS = r"""
() => {
  const URN_RE = /urn:li:(?:activity|ugcPost|share):\d+/;
  const ACTIVITY_RE = /urn:li:activity:\d+/;
  const isActivity = u => u.startsWith('urn:li:activity:');
  const TEXT_SEL = '.update-components-update-v2__commentary, .feed-shared-inline-show-more-text, .update-components-text, [data-testid="expandable-text-box"]';
  const AUTHOR_SEL = '.update-components-actor__title, .update-components-actor__name';
  const MORE_TAIL_RE = /(…|\.\.\.)\s*(ещё|еще|развернуть|see more|more)\s*$/i;
  const MORE_BTN_RE = /^(…\s*)?(ещё|еще|развернуть|see more|more)$/i;
  const clean = s => (s || '').replace(/ /g, ' ').trim();

  const findUpdateUrn = (v, depth) => {
    if (!v || typeof v !== 'object' || depth > 10) return null;
    if (typeof v.updateUrn === 'string') {
      const m = v.updateUrn.match(URN_RE);
      if (m) return m[0];
    }
    for (const k of Object.keys(v)) {
      const r = findUpdateUrn(v[k], depth + 1);
      if (r) return r;
    }
    return null;
  };

  const buildItem = (el, urn) => {
    const textEl = el.querySelector(TEXT_SEL);
    let text = clean(textEl ? textEl.innerText : el.innerText);
    let truncated = false;
    if (MORE_TAIL_RE.test(text)) {
      text = text.replace(MORE_TAIL_RE, '').trim();
      truncated = true;
    }
    for (const b of el.querySelectorAll('button')) {
      if (MORE_BTN_RE.test(clean(b.innerText))) { truncated = true; break; }
    }
    if (text.length < 80) truncated = true;
    let author = null;
    const authorEl = el.querySelector(AUTHOR_SEL);
    if (authorEl) author = clean(authorEl.innerText).split('\n')[0] || null;
    if (!author) {
      for (const a of el.querySelectorAll("a[href*='/in/'], a[href*='/company/']")) {
        author = clean(a.innerText).split('\n')[0] || null;
        if (author) break;
      }
    }
    return { urn, text, author, truncated };
  };

  const out = [];
  const seen = new Set();
  const accepted = [];
  const accept = (el, urn) => {
    if (!urn || seen.has(urn)) return;
    if (accepted.some(a => a.contains(el))) return;
    seen.add(urn);
    accepted.push(el);
    out.push(buildItem(el, urn));
  };

  for (const el of document.querySelectorAll('[data-view-tracking-scope]')) {
    const raw = el.getAttribute('data-view-tracking-scope') || '';
    let urn = null;
    try { urn = findUpdateUrn(JSON.parse(raw), 0); } catch (e) {}
    if (urn && !isActivity(urn)) {
      // the same post can be referenced as ugcPost/share and activity: prefer the activity form
      const m = raw.match(ACTIVITY_RE);
      if (m) urn = m[0];
    }
    accept(el, urn);
  }
  for (const el of document.querySelectorAll('[data-urn], [data-id]')) {
    const m = (el.getAttribute('data-urn') || el.getAttribute('data-id') || '').match(URN_RE);
    accept(el, m ? m[0] : null);
  }
  if (out.length) return { strategy: 'container', posts: out };

  // SDUI markup (2026): no URN or post links in the DOM. Each post card is
  // [componentkey="update-card-focus<hash>FeedType_…"]; its comment block carries
  // componentkey="<base64 protobuf with the post id>-replaceableCommentTools<hash>…".
  // The key is decoded in Python (sdui_key_to_urn).
  const sdui = [];
  for (const card of document.querySelectorAll('[componentkey^="update-card-focus"]')) {
    const hash = card.getAttribute('componentkey').replace(/^update-card-focus/, '').replace(/FeedType_[A-Z_]+$/, '');
    if (!hash) continue;
    const tools = card.querySelector('[componentkey*="-replaceableCommentTools' + hash + '"]');
    if (!tools) continue;
    const sduiKey = tools.getAttribute('componentkey').split('-replaceableCommentTools')[0];
    sdui.push(Object.assign(buildItem(card, null), { sduiKey }));
  }
  if (sdui.length) return { strategy: 'sdui', posts: sdui };

  const urnOf = a => {
    const m = (a.getAttribute('href') || '').match(URN_RE);
    return m ? m[0] : null;
  };
  // A post card may link to itself in several URN forms (activity + ugcPost/share). A node still
  // belongs to a single post while it holds at most one activity URN and at most one other-form URN.
  const urnsIn = node => new Set(Array.from(node.querySelectorAll("a[href*='urn:li:']")).map(urnOf).filter(Boolean));
  const singlePost = urns => {
    const list = Array.from(urns);
    return list.filter(isActivity).length <= 1 && list.filter(u => !isActivity(u)).length <= 1;
  };
  for (const a of document.querySelectorAll("a[href*='urn:li:']")) {
    const linkUrn = urnOf(a);
    if (!linkUrn || seen.has(linkUrn)) continue;
    let node = a.parentElement;
    let best = null;
    let bestUrns = null;
    while (node && node !== document.body && node.tagName !== 'MAIN') {
      const urns = urnsIn(node);
      if (!singlePost(urns)) break;
      best = node;
      bestUrns = urns;
      node = node.parentElement;
    }
    if (!best || !clean(best.innerText)) continue;
    const urn = Array.from(bestUrns).find(isActivity) || linkUrn;
    accept(best, urn);
    // the card is one post: its other URN forms must not produce extra rows
    if (accepted.includes(best)) for (const u of bestUrns) seen.add(u);
  }
  return { strategy: out.length ? 'links' : 'none', posts: out };
}
"""


SDUI_URN_KINDS = {1: "activity", 2: "ugcPost"}


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[i]
        value |= (byte & 0x7F) << shift
        i += 1
        shift += 7
        if not byte & 0x80:
            return value, i


def sdui_key_to_urn(key: str) -> str | None:
    """Decode the post id from an SDUI comment-tools key.

    The key is base64url protobuf: field 1 (activity) or 2 (ugcPost) holds a message whose
    field 1 is the zigzag-encoded id. Verified against live posts on 2026-09-17.
    """
    try:
        data = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
        kind = SDUI_URN_KINDS.get(data[0] >> 3)
        length, start = _varint(data, 1)
        inner = data[start:start + length]
        if kind is None or data[0] & 0x07 != 2 or not inner or inner[0] != 0x08:
            return None
        zigzag, _ = _varint(inner, 1)
        post_id = (zigzag >> 1) ^ -(zigzag & 1)
    except (ValueError, IndexError):
        return None
    return f"urn:li:{kind}:{post_id}" if post_id > 0 else None


async def extract_posts(page) -> tuple[list[dict], str]:
    res = await page.evaluate(EXTRACT_JS)
    posts, seen = [], set()
    for p in res["posts"]:
        key = p.pop("sduiKey", None)
        urn = sdui_key_to_urn(key) if key is not None else p["urn"]
        if not urn or urn in seen:
            continue
        seen.add(urn)
        posts.append(dict(p, urn=urn, url=post_url(urn)))
    return posts, res["strategy"]

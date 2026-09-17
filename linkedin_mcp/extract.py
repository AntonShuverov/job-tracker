EXTRACT_JS = r"""
() => {
  const URN_RE = /urn:li:(?:activity|ugcPost|share):\d+/;
  const ACTIVITY_RE = /urn:li:activity:\d+/;
  const isActivity = u => u.startsWith('urn:li:activity:');
  const TEXT_SEL = '.update-components-update-v2__commentary, .feed-shared-inline-show-more-text, .update-components-text';
  const AUTHOR_SEL = '.update-components-actor__title, .update-components-actor__name';
  const MORE_TAIL_RE = /(…|\.\.\.)\s*(ещё|еще|see more|more)\s*$/i;
  const MORE_BTN_RE = /^(…\s*)?(ещё|еще|see more|more)$/i;
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
      const a = el.querySelector("a[href*='/in/']");
      if (a) author = clean(a.innerText).split('\n')[0] || null;
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


def post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/"


async def extract_posts(page) -> tuple[list[dict], str]:
    res = await page.evaluate(EXTRACT_JS)
    posts = [dict(p, url=post_url(p["urn"])) for p in res["posts"]]
    return posts, res["strategy"]

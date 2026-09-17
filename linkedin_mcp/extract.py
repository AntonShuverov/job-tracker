EXTRACT_JS = r"""
() => {
  const URN_RE = /urn:li:(?:activity|ugcPost|share):\d+/;
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
    let urn = null;
    try { urn = findUpdateUrn(JSON.parse(el.getAttribute('data-view-tracking-scope')), 0); } catch (e) {}
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
  for (const a of document.querySelectorAll("a[href*='urn:li:']")) {
    const urn = urnOf(a);
    if (!urn || seen.has(urn)) continue;
    let node = a.parentElement;
    let best = null;
    while (node && node !== document.body && node.tagName !== 'MAIN') {
      const other = Array.from(node.querySelectorAll("a[href*='urn:li:']")).some(x => {
        const u = urnOf(x);
        return u && u !== urn;
      });
      if (other) break;
      best = node;
      node = node.parentElement;
    }
    if (best && clean(best.innerText)) accept(best, urn);
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

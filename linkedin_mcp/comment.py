import asyncio
import random
import re
import time
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import pause

OPEN_RE = re.compile(r"^\s*(Комментировать|Comment)\s*$", re.IGNORECASE)
SUBMIT_RE = re.compile(r"^\s*(Комментировать|Опубликовать|Comment|Post)\s*$", re.IGNORECASE)
EDITOR_SEL = "div[contenteditable='true'][role='textbox'], .ql-editor[contenteditable='true']"
SUBMIT_FALLBACK_SEL = "button.comments-comment-box__submit-button:not([disabled])"

FIND_PUBLISHED_JS = r"""
(root, snippet) => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  for (const el of root.querySelectorAll('*')) {
    if (el.closest('[contenteditable="true"]') || el.querySelector('[contenteditable="true"]')) continue;
    if (['SCRIPT', 'STYLE'].includes(el.tagName)) continue;
    if (norm(el.innerText).includes(snippet)) return true;
  }
  return false;
}
"""


async def _post_scope(page):
    for sel in ("[data-view-tracking-scope]", "article", "main", "body"):
        loc = page.locator(sel)
        if await loc.count():
            return loc.first
    return page.locator("body")


async def _is_within(loc, scope_handle) -> bool:
    if not await loc.count():
        return False
    handle = await loc.element_handle()
    if handle is None:
        return False
    return await scope_handle.evaluate("(scopeEl, el) => scopeEl.contains(el)", handle)


async def _find_submit(editor, scope):
    scope_handle = await scope.element_handle()
    if scope_handle is None:
        return None

    depth = await editor.evaluate(
        """(el, scopeEl) => {
            let cur = el.parentElement, d = 0;
            while (cur) {
                d++;
                if (cur === scopeEl) return d;
                cur = cur.parentElement;
            }
            return -1;
        }""",
        scope_handle,
    )
    if depth is None or depth < 0:
        depth = 0

    scopes = []
    form = editor.locator("xpath=ancestor::form[1]")
    if await form.count() and await _is_within(form, scope_handle):
        scopes.append(form)
    scopes += [editor.locator(f"xpath=ancestor::*[{level}]") for level in range(1, depth + 1)]

    for scope_loc in scopes:
        btn = scope_loc.locator("button:not([disabled])").filter(has_text=SUBMIT_RE)
        if await btn.count():
            return btn.last
        fallback = scope_loc.locator(SUBMIT_FALLBACK_SEL)
        if await fallback.count():
            return fallback.first
    return None


async def _wait_published(scope, editor, text: str, wait_ms: int) -> bool:
    snippet = " ".join(text.split())[:40]
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline:
        editor_empty = (not await editor.count()) or not (await editor.inner_text()).strip()
        if editor_empty and await scope.evaluate(FIND_PUBLISHED_JS, snippet):
            return True
        await asyncio.sleep(0.5)
    return False


async def _fail(page, debug_dir: Path, code: str) -> dict:
    shot = None
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"comment_fail_{int(time.time())}.png"
        await page.screenshot(path=str(path))
        shot = str(path)
    except Exception:
        pass
    return {"ok": False, "error": code, "screenshot": shot}


async def publish_on_page(page, text: str, debug_dir: Path, wait_ms: int = 15000) -> dict:
    scope = await _post_scope(page)
    editor = scope.locator(EDITOR_SEL).first
    if not (await editor.count() and await editor.is_visible()):
        opener = scope.get_by_role("button", name=OPEN_RE)
        if await opener.count():
            await opener.first.click()
            await pause.human_pause(1.0, 2.0)
    try:
        await editor.wait_for(state="visible", timeout=wait_ms)
    except PlaywrightTimeoutError:
        return await _fail(page, debug_dir, "editor_not_found")

    await editor.click()
    await editor.press_sequentially(text, delay=random.randint(35, 90))
    await pause.human_pause(1.5, 3.0)

    submit = await _find_submit(editor, scope)
    if submit is None:
        return await _fail(page, debug_dir, "submit_not_found")
    await submit.click()

    if await _wait_published(scope, editor, text, wait_ms):
        return {"ok": True}
    return await _fail(page, debug_dir, "not_confirmed")

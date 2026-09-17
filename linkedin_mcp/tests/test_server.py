import asyncio
import json
from dataclasses import replace

from mcp import Client

from linkedin_mcp.config import load_settings
from linkedin_mcp.server import build_server
from linkedin_mcp.tests.conftest import FIXTURES

TOOLS = {"linkedin_login", "search_posts", "get_post", "list_unreviewed", "save_vacancy", "mark_not_vacancy",
         "list_vacancies", "set_vacancy_status", "publish_comment", "list_comments", "limits_status",
         "export_obsidian"}
COMMENT = "Здравствуйте! Интересна позиция продакта, 5 лет в финтехе. Буду рад пообщаться."


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.lock = asyncio.Lock()
        self.notice = None
        self.visited = []

    async def goto(self, url):
        self.visited.append(url)
        name = "search_tracking_scope.html" if "/search/results/" in url else "post_with_comment_box.html"
        await self.page.set_content((FIXTURES / name).read_text(encoding="utf-8"))
        return self.page

    async def login(self, timeout_s=300):
        return {"ok": True, "message": "fake"}

    async def close(self):
        pass


class BrokenCommentBrowser(FakeBrowser):
    """Like FakeBrowser, but the post page never renders a submitted comment, so
    comment.publish_on_page always times out with error 'not_confirmed'.

    Navigates to about:blank before each set_content: repeatedly calling
    page.set_content() on the same document (without a real navigation in between)
    leaves inline <script> onclick handlers unresponsive to clicks on the second and
    later loads in this Chromium/Playwright combination — a test-harness quirk, not a
    behavior of comment.py (production code always does a real page.goto navigation
    between posts). The extra about:blank hop forces a clean document each time.
    """

    async def goto(self, url):
        await self.page.goto("about:blank")
        page = await super().goto(url)
        if "/feed/update/" in url:
            await page.evaluate("window.BROKEN = true")
        return page


def _settings(tmp_path):
    return replace(load_settings(), db_path=tmp_path / "t.db", obsidian_path=tmp_path / "vault" / "LinkedIn.md",
                   debug_dir=tmp_path / "debug", limits={"search": 2, "post_open": 5, "comment": 8})


async def _call(client, name, args=None):
    r = await client.call_tool(name, args or {})
    # Dict-returning tools: a single TextContent with the full JSON object, structured_content is None.
    # List-returning tools: one TextContent per list item (each only a fragment) plus
    # structured_content = {"result": [...]} holding the actual list — read from there instead.
    if r.structured_content is not None:
        return r.structured_content.get("result", r.structured_content)
    return json.loads(r.content[0].text)


async def test_tools_listed(page, tmp_path):
    async with Client(build_server(_settings(tmp_path), FakeBrowser(page))) as c:
        names = {t.name for t in (await c.list_tools()).tools}
    assert names == TOOLS


async def test_full_flow(page, tmp_path):
    s = _settings(tmp_path)
    fake = FakeBrowser(page)
    async with Client(build_server(s, fake)) as c:
        res = await _call(c, "search_posts", {"query": "ищем продакта"})
        assert res["found"] == 3 and res["new"] == 3 and res["strategy"] == "container"
        assert res["posts"][0]["url"].startswith("https://www.linkedin.com/feed/update/urn:li:activity:")

        again = await _call(c, "search_posts", {"query": "ищем продакта"})
        assert again["new"] == 0 and again["already_seen"] == 3 and again["posts"] == []

        limited = await _call(c, "search_posts", {"query": "третий"})
        assert limited["error"] == "limit_reached"

        assert len(await _call(c, "list_unreviewed")) == 3
        saved = await _call(c, "save_vacancy", {"urn": "urn:li:activity:111", "title": "Product Manager",
                                                "company": "T-Bank"})
        assert saved["ok"] is True
        assert (await _call(c, "mark_not_vacancy", {"urns": ["urn:li:activity:222", "urn:li:activity:333"]}))["updated"] == 2
        assert (s.obsidian_path).read_text(encoding="utf-8").count("T-Bank") == 1

        bad = await _call(c, "publish_comment", {"urn": "urn:li:activity:222", "text": COMMENT})
        assert bad["error"] == "not_vacancy"

        ok = await _call(c, "publish_comment", {"urn": "urn:li:activity:111", "text": COMMENT})
        assert ok["ok"] is True
        dup = await _call(c, "publish_comment", {"urn": "urn:li:activity:111", "text": COMMENT})
        assert dup["error"] == "already_commented"

        vac = await _call(c, "list_vacancies")
        assert vac[0]["status"] == "commented" and vac[0]["comment"] == COMMENT
        log = await _call(c, "list_comments")
        assert [x["status"] for x in log] == ["published"]

        st = await _call(c, "set_vacancy_status", {"urn": "urn:li:activity:111", "status": "applied"})
        assert st["ok"] is True
        wrong = await _call(c, "set_vacancy_status", {"urn": "urn:li:activity:111", "status": "hired"})
        assert wrong["error"] == "bad_status"

        lim = await _call(c, "limits_status")
        assert lim["search"]["used"] == 2 and lim["comment"]["used"] == 1

        got = await _call(c, "get_post", {"urn": "urn:li:activity:111"})
        assert got["author"] == "Анна Первая" and "продакт-менеджера" in got["text"]
        exp = await _call(c, "export_obsidian")
        assert exp == {"ok": True, "path": str(s.obsidian_path)}
    assert "## Откликнулся (1)" in s.obsidian_path.read_text(encoding="utf-8")


async def test_list_vacancies_bad_status(page, tmp_path):
    async with Client(build_server(_settings(tmp_path), FakeBrowser(page))) as c:
        res = await _call(c, "list_vacancies", {"status": "hired"})
        assert res["error"] == "bad_status"


async def test_publish_comment_uncertain_previous_attempt(page, tmp_path, monkeypatch):
    from linkedin_mcp import comment as comment_mod

    orig_publish = comment_mod.publish_on_page

    async def fast_publish(page, text, debug_dir, wait_ms=15000):
        return await orig_publish(page, text, debug_dir, wait_ms=1000)

    monkeypatch.setattr(comment_mod, "publish_on_page", fast_publish)

    s = _settings(tmp_path)
    fake = BrokenCommentBrowser(page)
    async with Client(build_server(s, fake)) as c:
        await _call(c, "search_posts", {"query": "ищем продакта"})
        await _call(c, "save_vacancy", {"urn": "urn:li:activity:111", "title": "Product Manager",
                                        "company": "T-Bank"})

        first = await _call(c, "publish_comment", {"urn": "urn:li:activity:111", "text": COMMENT})
        assert first["error"] == "not_confirmed" and first["message"]

        retry = await _call(c, "publish_comment", {"urn": "urn:li:activity:111", "text": COMMENT})
        assert retry["error"] == "uncertain_previous_attempt"

        confirmed = await _call(c, "publish_comment",
                                {"urn": "urn:li:activity:111", "text": COMMENT, "confirm_not_posted": True})
        assert confirmed["error"] == "not_confirmed" and confirmed["message"]

        log = await _call(c, "list_comments")
        assert [x["status"] for x in log] == ["failed", "failed"]

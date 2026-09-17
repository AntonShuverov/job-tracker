from linkedin_mcp import comment
from linkedin_mcp.tests.conftest import FIXTURES

TEXT = "Здравствуйте! Интересна позиция продакта, 5 лет в финтехе."


async def _load(page):
    await page.set_content((FIXTURES / "post_with_comment_box.html").read_text(encoding="utf-8"))


async def test_publish_success(page, tmp_path):
    await _load(page)
    res = await comment.publish_on_page(page, TEXT, tmp_path, wait_ms=5000)
    assert res == {"ok": True}
    assert await page.locator(".comments-list article").inner_text() == TEXT
    assert await page.evaluate("window.otherClicked") is False


async def test_publish_not_confirmed(page, tmp_path):
    await _load(page)
    await page.evaluate("window.BROKEN = true")
    res = await comment.publish_on_page(page, TEXT, tmp_path, wait_ms=1500)
    assert res["ok"] is False and res["error"] == "not_confirmed"
    assert res["screenshot"] and (tmp_path / res["screenshot"].split("/")[-1]).exists()


async def test_editor_not_found(page, tmp_path):
    await page.set_content("<main><p>Пост без поля комментария</p></main>")
    res = await comment.publish_on_page(page, TEXT, tmp_path, wait_ms=1000)
    assert res["ok"] is False and res["error"] == "editor_not_found"

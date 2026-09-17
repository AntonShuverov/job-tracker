from linkedin_mcp import extract, search
from linkedin_mcp.tests.conftest import FIXTURES


async def _load(page, name):
    await page.set_content((FIXTURES / name).read_text(encoding="utf-8"))


async def test_container_strategy(page):
    await _load(page, "search_tracking_scope.html")
    posts, strategy = await extract.extract_posts(page)
    assert strategy == "container"
    by_urn = {p["urn"]: p for p in posts}
    assert list(by_urn) == ["urn:li:activity:111", "urn:li:activity:222", "urn:li:activity:333"]
    assert by_urn["urn:li:activity:111"]["author"] == "Анна Первая"
    assert by_urn["urn:li:activity:111"]["text"].startswith("Ищем продакт-менеджера в команду платежей")
    assert by_urn["urn:li:activity:111"]["truncated"] is False
    assert by_urn["urn:li:activity:111"]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:111/"
    assert by_urn["urn:li:activity:222"]["truncated"] is True
    assert not by_urn["urn:li:activity:222"]["text"].endswith("ещё")
    assert by_urn["urn:li:activity:333"]["author"] == "Вера Третья"
    assert "Head of Product" in by_urn["urn:li:activity:333"]["text"]


async def test_links_fallback(page):
    await _load(page, "search_links_only.html")
    posts, strategy = await extract.extract_posts(page)
    assert strategy == "links"
    by_urn = {p["urn"]: p for p in posts}
    assert set(by_urn) == {"urn:li:activity:444", "urn:li:activity:555"}
    assert "логистики" in by_urn["urn:li:activity:444"]["text"]
    assert "Короткий пост" not in by_urn["urn:li:activity:444"]["text"]
    assert by_urn["urn:li:activity:444"]["author"] == "Глеб Четвёртый"
    assert by_urn["urn:li:activity:555"]["truncated"] is True


def test_search_url():
    assert search.search_url("ищем продакта", 1).endswith("datePosted=%22past-24h%22")
    assert "keywords=%D0%B8%D1%89%D0%B5%D0%BC%20" in search.search_url("ищем продакта", 7)
    assert search.search_url("pm", 7).endswith("sortBy=%22date_posted%22&datePosted=%22past-week%22")
    assert search.search_url("pm", 30).endswith("datePosted=%22past-month%22")


async def test_collect_posts_limits(page):
    await _load(page, "search_tracking_scope.html")
    posts, strategy = await search.collect_posts(page, max_posts=2)
    assert len(posts) == 2 and strategy == "container"
    posts, _ = await search.collect_posts(page, max_posts=30)
    assert len(posts) == 3


async def test_run_on_page_warns_and_dumps(page, tmp_path):
    await page.set_content("<main>" + "Навигация и прочий текст страницы. " * 20 + "</main>")
    res = await search.run_on_page(page, 30, tmp_path)
    assert res["posts"] == [] and "селекторы" in res["warning"]
    assert len(list(tmp_path.glob("search_*.html"))) == 1


async def test_run_on_page_no_results_no_warning(page, tmp_path):
    await page.set_content("<main>" + "Навигация. " * 40 + "<h2>Результатов не найдено</h2></main>")
    res = await search.run_on_page(page, 30, tmp_path)
    assert res["posts"] == [] and "warning" not in res
    assert list(tmp_path.iterdir()) == []


async def test_read_post_page(page):
    await _load(page, "search_tracking_scope.html")
    post = await search.read_post_page(page, "urn:li:activity:333")
    assert post["author"] == "Вера Третья"
    assert await search.read_post_page(page, "urn:li:activity:999") is None


LONG = "Ищем продакт-менеджера в команду платежей, удалёнка, опыт от трёх лет, пишите в личные сообщения."


async def test_links_fallback_prefers_activity_over_ugcpost(page):
    await page.set_content(f"""<main><ul>
      <li><a href="/in/a"><span>Автор А</span></a><p>{LONG}</p>
        <a href="/feed/update/urn:li:ugcPost:901/">пост</a>
        <a href="/feed/update/urn:li:activity:444/">2 дн.</a></li>
      <li><a href="/in/b"><span>Автор Б</span></a><p>Второй пост про вакансию аналитика данных, офис в Алматы, гибрид.</p>
        <a href="/feed/update/urn:li:share:902/">пост</a>
        <a href="/feed/update/urn:li:activity:555/">3 дн.</a></li>
    </ul></main>""")
    posts, strategy = await extract.extract_posts(page)
    assert strategy == "links"
    assert [p["urn"] for p in posts] == ["urn:li:activity:444", "urn:li:activity:555"]
    assert "платежей" in posts[0]["text"] and "аналитика" not in posts[0]["text"]


async def test_container_prefers_activity_urn_in_tracking_scope(page):
    await page.set_content(f"""<main>
      <div data-view-tracking-scope='[{{"breadcrumb":{{"updateUrn":"urn:li:ugcPost:901","content":{{"urn":"urn:li:activity:444"}}}}}}]'>
        <div class="update-components-update-v2__commentary">{LONG}</div></div>
    </main>""")
    posts, strategy = await extract.extract_posts(page)
    assert strategy == "container"
    assert [p["urn"] for p in posts] == ["urn:li:activity:444"]

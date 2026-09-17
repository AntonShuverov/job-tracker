import random
import re
import time
from pathlib import Path
from urllib.parse import quote

from . import extract, pause

PERIODS = [(1, "past-24h"), (7, "past-week")]
SHOW_MORE_RE = re.compile(r"^(Показать больше результатов|Show more results)$", re.IGNORECASE)
NO_RESULTS_RE = re.compile(r"Результатов не найдено|Ничего не найдено|Поиск не дал результатов|No results found", re.IGNORECASE)


def search_url(query: str, days: int) -> str:
    period = "past-month"
    for max_days, name in PERIODS:
        if days <= max_days:
            period = name
            break
    return ("https://www.linkedin.com/search/results/content/?keywords=" + quote(query)
            + "&sortBy=%22date_posted%22&datePosted=%22" + period + "%22")


async def collect_posts(page, max_posts: int, max_steps: int = 15) -> tuple[list[dict], str]:
    collected: dict[str, dict] = {}
    strategy = "none"
    idle = 0
    for _ in range(max_steps):
        posts, strat = await extract.extract_posts(page)
        before = len(collected)
        for p in posts:
            collected.setdefault(p["urn"], p)
        if posts:
            strategy = strat
        if len(collected) >= max_posts:
            break
        idle = idle + 1 if len(collected) == before else 0
        if idle >= 2:
            break
        more = page.get_by_role("button", name=SHOW_MORE_RE)
        if await more.count():
            await more.first.click()
        else:
            await page.mouse.wheel(0, random.randint(600, 1100))
        await pause.human_pause(1.2, 3.0)
    return list(collected.values())[:max_posts], strategy


async def run_on_page(page, max_posts: int, debug_dir: Path) -> dict:
    posts, strategy = await collect_posts(page, max_posts)
    result = {"posts": posts, "strategy": strategy}
    if not posts:
        body = await page.inner_text("body")
        if len(body.strip()) > 200 and not NO_RESULTS_RE.search(body):
            debug_dir.mkdir(parents=True, exist_ok=True)
            dump = debug_dir / f"search_{int(time.time())}.html"
            dump.write_text(await page.content(), encoding="utf-8")
            result["warning"] = f"0 постов на непустой странице — вероятно, селекторы устарели. HTML: {dump}"
    return result


async def read_post_page(page, urn: str) -> dict | None:
    posts, _ = await extract.extract_posts(page)
    for p in posts:
        if p["urn"] == urn:
            return p
    return None

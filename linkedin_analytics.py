"""
linkedin_analytics.py — сбор аналитики с опубликованных LinkedIn постов.

Запуск: python3 linkedin_analytics.py

Читает посты со статусом "Опубликован" и ссылкой, открывает каждый пост
в браузере, парсит метрики и обновляет Notion.

Запускать через 24-48 часов после публикации для значимых данных.
"""

import os
import logging
import time
import random
import re
import requests
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

from common import get_notion_headers

# ── Константы ─────────────────────────────────────────────────────────────────

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, "linkedin_session.json")
LOG_FILE     = os.path.join(BASE_DIR, "linkedin_analytics.log")
CONTENT_DB   = os.getenv("LINKEDIN_CONTENT_DB_ID", "")
NOTION_API   = "https://api.notion.com/v1"

# ── Логгирование ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("linkedin_analytics")


# ── Notion helpers ────────────────────────────────────────────────────────────

def get_published_posts() -> list[dict]:
    """
    Query Notion for posts with status=Опубликован and non-empty Ссылка.
    Returns list of dicts: {id, title, url}.
    """
    results = []
    cursor = None

    while True:
        body = {
            "filter": {
                "and": [
                    {"property": "Статус", "select": {"equals": "Опубликован"}},
                    {"property": "Ссылка", "url": {"is_not_empty": True}},
                ]
            },
            "page_size": 100,
        }
        if cursor:
            body["start_cursor"] = cursor

        try:
            r = requests.post(
                f"{NOTION_API}/databases/{CONTENT_DB}/query",
                headers=get_notion_headers(),
                json=body,
                timeout=15,
            )
            if r.status_code != 200:
                log.error(f"Notion query failed: {r.status_code} {r.text[:200]}")
                break
            data = r.json()
        except Exception as e:
            log.error(f"Notion query error: {e}")
            break

        for page in data.get("results", []):
            props = page["properties"]
            url = props.get("Ссылка", {}).get("url") or ""
            if not url:
                continue
            title_items = props.get("Заголовок", {}).get("title", [])
            title = "".join(i["plain_text"] for i in title_items) if title_items else "(без заголовка)"
            results.append({"id": page["id"], "title": title, "url": url})

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return results


def update_notion_analytics(page_id: str, metrics: dict) -> bool:
    """
    Update only metrics that are not None.
    metrics keys: reactions, comments, reposts, views
    """
    field_map = {
        "reactions": "👍 Реакции",
        "comments":  "💬 Комментарии",
        "reposts":   "🔄 Репосты",
        "views":     "👀 Просмотры",
    }
    props = {}
    for key, notion_field in field_map.items():
        val = metrics.get(key)
        if val is not None:
            props[notion_field] = {"number": val}

    if not props:
        log.info("   ⏭  Метрики не найдены, пропускаем обновление")
        return False

    try:
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=get_notion_headers(),
            json={"properties": props},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Notion analytics update error: {e}")
    return False


# ── Metrics parsing ───────────────────────────────────────────────────────────

def parse_number(text: str) -> int | None:
    """Extract integer from strings like '1 234' or '12K' or '5'."""
    if not text:
        return None
    # Strip all whitespace (including embedded newlines from regex captures)
    text = re.sub(r"\s+", "", text).replace("\u202f", "")
    # Handle K suffix: '12K' → 12000
    m = re.match(r"^(\d+(?:[.,]\d+)?)([Kk])?$", text)
    if m:
        num = float(m.group(1).replace(",", "."))
        if m.group(2):
            num *= 1000
        return int(num)
    return None


def scrape_metrics(page) -> dict:
    """
    Parse LinkedIn post metrics from the currently open page.
    Returns dict with keys: reactions, comments, reposts, views.
    Values are int or None if not found.

    Patterns target digit sequences immediately before metric words.
    Russian patterns are preferred; English are fallbacks.
    The English 'view' pattern uses a word boundary to avoid matching
    'View profile', 'View all comments', etc.
    """
    metrics = {"reactions": None, "comments": None, "reposts": None, "views": None}

    try:
        body_text = page.inner_text("body")
    except Exception:
        return metrics

    # Reactions: "1 234 реакц" or "45 reactions"
    m = re.search(r"(\d[\d\s]*)\s*реакц", body_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d[\d\s]*)\s*reactions?\b", body_text, re.IGNORECASE)
    if m:
        metrics["reactions"] = parse_number(m.group(1))

    # Comments: "3 коммент" or "3 comments"
    m = re.search(r"(\d[\d\s]*)\s*коммент", body_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d[\d\s]*)\s*comments?\b", body_text, re.IGNORECASE)
    if m:
        metrics["comments"] = parse_number(m.group(1))

    # Reposts: "2 репост" or "2 reposts"
    m = re.search(r"(\d[\d\s]*)\s*репост", body_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d[\d\s]*)\s*reposts?\b", body_text, re.IGNORECASE)
    if m:
        metrics["reposts"] = parse_number(m.group(1))

    # Views (only shown for posts with significant reach)
    # Use \bviews?\b word boundary — avoids 'View profile', 'Overview', etc.
    m = re.search(r"(\d[\d\s]*)\s*просмотр", body_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d[\d\s]*)\s*\bviews?\b", body_text, re.IGNORECASE)
    if m:
        metrics["views"] = parse_number(m.group(1))

    return metrics


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("📊 LinkedIn Analytics — сбор метрик\n")

    if not CONTENT_DB:
        log.error("❌ LINKEDIN_CONTENT_DB_ID не задан в .env")
        return

    posts = get_published_posts()
    if not posts:
        log.info("ℹ️  Нет опубликованных постов со ссылкой")
        return

    log.info(f"📋 Постов для обновления аналитики: {len(posts)}\n")

    pw      = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx     = browser.new_context(storage_state=SESSION_FILE)
    page    = ctx.new_page()

    # Verify session
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    if "authwall" in page.url or "login" in page.url:
        log.error("❌ Сессия LinkedIn истекла. Запусти: python3 linkedin_login.py")
        browser.close()
        pw.stop()
        return
    log.info("✅ Авторизация OK\n")

    try:
        for post in posts:
            log.info(f"🔍 «{post['title']}»")
            log.info(f"   URL: {post['url']}")

            try:
                page.goto(post["url"], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
            except Exception as e:
                log.warning(f"   ⚠️  Не удалось открыть пост: {e}")
                delay = random.uniform(3, 7)
                time.sleep(delay)
                continue

            if "authwall" in page.url or "login" in page.url:
                log.error("❌ Сессия истекла во время работы")
                break

            metrics = scrape_metrics(page)
            log.info(f"   📈 Реакции={metrics['reactions']} Комменты={metrics['comments']} "
                     f"Репосты={metrics['reposts']} Просмотры={metrics['views']}")

            updated = update_notion_analytics(post["id"], metrics)
            if updated:
                log.info("   ✅ Notion обновлён")
            else:
                log.info("   ⏭  Notion не обновлён (нет данных)")

            delay = random.uniform(3, 7)
            log.info(f"   ⏳ Пауза {delay:.0f}с...")
            time.sleep(delay)

    finally:
        browser.close()
        pw.stop()

    log.info("\n✅ Готово")


if __name__ == "__main__":
    main()

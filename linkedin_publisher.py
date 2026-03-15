"""
linkedin_publisher.py — публикация LinkedIn постов по расписанию из Notion.

Запуск: python3 linkedin_publisher.py

Читает посты со статусом "Запланирован" и датой публикации <= сегодня,
публикует каждый пост в LinkedIn через Playwright, обновляет Notion.
"""

import os
import logging
import time
import random
import requests
from datetime import date
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

load_dotenv()

from common import get_notion_headers

# ── Константы ─────────────────────────────────────────────────────────────────

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, "linkedin_session.json")
LOG_FILE     = os.path.join(BASE_DIR, "linkedin_publisher.log")
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
log = logging.getLogger("linkedin_publisher")


# ── Notion helpers ────────────────────────────────────────────────────────────

def extract_text(rich_text: list) -> str:
    """Concatenate plain_text from a Notion rich_text array."""
    return "".join(item["plain_text"] for item in rich_text)


def validate_post_text(text: str) -> tuple[bool, str | None]:
    """Return (True, None) if valid, (False, reason) otherwise."""
    if not text.strip():
        return False, "Текст поста пустой"
    if len(text) > 3000:
        return False, f"Текст превышает 3000 символов ({len(text)})"
    return True, None


def get_scheduled_posts() -> list[dict]:
    """
    Query Notion for posts with status=Запланирован and scheduled date <= today.
    Returns list of dicts: {id, title, text, scheduled_date}.
    """
    today = date.today()
    results = []
    cursor = None

    while True:
        body = {
            "filter": {
                "property": "Статус",
                "select": {"equals": "Запланирован"},
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

            # Get scheduled date
            # Note: trailing space is intentional — it's the actual Notion field name
            date_field = props.get("Дата когда нужно опубликовать ", {}).get("date")
            if not date_field:
                continue
            scheduled = date.fromisoformat(date_field["start"])
            if scheduled > today:
                continue

            # Get text
            rich_text = props.get("Текст поста", {}).get("rich_text", [])
            text = extract_text(rich_text)

            # Get title
            title_items = props.get("Заголовок", {}).get("title", [])
            title = extract_text(title_items) if title_items else "(без заголовка)"

            results.append({
                "id": page["id"],
                "title": title,
                "text": text,
                "scheduled_date": scheduled.isoformat(),
            })

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return results


def update_notion_published(page_id: str, publish_date: str, post_url: str) -> bool:
    """Mark page as published with date and URL."""
    props = {
        "Статус":           {"select": {"name": "Опубликован"}},
        "Дата публикации":  {"date": {"start": publish_date}},
        "Ссылка":           {"url": post_url},
    }
    try:
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=get_notion_headers(),
            json={"properties": props},
            timeout=15,
        )
        if r.status_code == 200:
            return True
        log.error(f"Notion update failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log.error(f"Notion update error: {e}")
    return False


def update_notion_error(page_id: str, reason: str) -> bool:
    """Mark page as error and write reason to Выводы."""
    props = {
        "Статус":  {"select": {"name": "Ошибка"}},
        "Выводы":  {"rich_text": [{"text": {"content": reason[:2000]}}]},
    }
    try:
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=get_notion_headers(),
            json={"properties": props},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Notion error update failed: {e}")
    return False


def update_notion_url_missing(page_id: str, publish_date: str) -> bool:
    """Mark as published but URL not captured — write note to Выводы."""
    props = {
        "Статус":          {"select": {"name": "Опубликован"}},
        "Дата публикации": {"date": {"start": publish_date}},
        "Выводы":          {"rich_text": [{"text": {"content": "URL не захвачен — проверь вручную"}}]},
    }
    try:
        r = requests.patch(
            f"{NOTION_API}/pages/{page_id}",
            headers=get_notion_headers(),
            json={"properties": props},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        log.error(f"Notion url-missing update failed: {e}")
    return False


# ── LinkedIn publishing ───────────────────────────────────────────────────────

def capture_post_url(page) -> str | None:
    """
    Navigate to author's recent activity and grab the URL of the most recent post.
    Looks for /feed/update/urn:li:activity: pattern specifically.
    Returns URL string or None if not found.
    """
    try:
        page.goto(
            "https://www.linkedin.com/in/me/recent-activity/all/",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        page.wait_for_timeout(4000)

        # Find all links and filter for the post URL pattern
        links = page.query_selector_all("a[href*='/feed/update/']")
        for link in links:
            href = link.get_attribute("href") or ""
            if "/feed/update/" in href and "activity" in href:
                if href.startswith("/"):
                    href = "https://www.linkedin.com" + href
                return href.split("?")[0]
    except Exception as e:
        log.warning(f"capture_post_url error: {e}")
    return None


def publish_post(page, text: str) -> bool:
    """
    Click 'Start a post', type text, click 'Post'.
    Returns True if post button was clicked successfully.
    """
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Click "Написать пост" / "Start a post"
        start_btn = (
            page.query_selector("button.share-box-feed-entry__trigger") or
            page.query_selector("button[aria-label*='Написать пост']") or
            page.query_selector("button[aria-label*='Start a post']") or
            page.query_selector("div.share-box-feed-entry__top-bar button")
        )
        if not start_btn:
            log.error("Кнопка 'Написать пост' не найдена")
            return False

        start_btn.click()
        page.wait_for_timeout(2000)

        # Type into editor
        editor = (
            page.query_selector("div.ql-editor") or
            page.query_selector("div[data-placeholder*='пост']") or
            page.query_selector("div[contenteditable='true']")
        )
        if not editor:
            log.error("Редактор поста не найден")
            return False

        editor.click()
        page.keyboard.type(text, delay=20)
        page.wait_for_timeout(1500)

        # Click "Опубликовать" / "Post"
        post_btn = (
            page.query_selector("button.share-actions__primary-action") or
            page.query_selector("button[aria-label*='Опубликовать']") or
            page.query_selector("button[aria-label*='Post']") or
            page.query_selector("button.artdeco-button--primary:has-text('Опубликовать')") or
            page.query_selector("button.artdeco-button--primary:has-text('Post')")
        )
        if not post_btn:
            log.error("Кнопка 'Опубликовать' не найдена")
            return False

        post_btn.click()
        page.wait_for_timeout(4000)
        return True

    except Exception as e:
        log.error(f"publish_post error: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log.info("📝 LinkedIn Publisher — публикация по расписанию\n")

    if not CONTENT_DB:
        log.error("❌ LINKEDIN_CONTENT_DB_ID не задан в .env")
        return

    posts = get_scheduled_posts()
    if not posts:
        log.info("ℹ️  Нет запланированных постов на сегодня")
        return

    log.info(f"📋 Найдено постов для публикации: {len(posts)}\n")

    pw      = sync_playwright().start()
    browser = pw.chromium.launch(headless=False)
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
            log.info(f"📌 «{post['title']}»")

            # Validate text
            ok, reason = validate_post_text(post["text"])
            if not ok:
                log.warning(f"   ⚠️  {reason}")
                update_notion_error(post["id"], reason)
                continue

            # Publish
            published = publish_post(page, post["text"])
            if not published:
                reason = "Не удалось опубликовать пост — кнопка не найдена"
                log.error(f"   ❌ {reason}")
                update_notion_error(post["id"], reason)
                continue

            log.info("   ✅ Пост опубликован")

            # Capture URL
            post_url = capture_post_url(page)
            today_str = date.today().isoformat()

            if post_url:
                log.info(f"   🔗 {post_url}")
                update_notion_published(post["id"], today_str, post_url)
            else:
                log.warning("   ⚠️  URL поста не захвачен — обновляю Notion без ссылки")
                update_notion_url_missing(post["id"], today_str)

            # Delay between posts
            if len(posts) > 1:
                delay = random.uniform(10, 20)
                log.info(f"   ⏳ Пауза {delay:.0f}с перед следующим постом...")
                time.sleep(delay)

    finally:
        browser.close()
        pw.stop()

    log.info("\n✅ Готово")


if __name__ == "__main__":
    main()

# LinkedIn Content Publisher Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two standalone scripts — `linkedin_publisher.py` publishes scheduled LinkedIn posts from Notion, `linkedin_analytics.py` collects post metrics back into Notion.

**Architecture:** Playwright-based browser automation (headless=False for publishing, headless=True for analytics). Both scripts read/write to a dedicated Notion DB. Non-browser logic (Notion queries, text validation, payload building) is unit-tested with mocked HTTP; browser flows are verified manually.

**Tech Stack:** Python 3.11+, Playwright, requests, python-dotenv, pytest, unittest.mock

**Spec:** `docs/superpowers/specs/2026-03-15-linkedin-content-publisher-design.md`

---

## Chunk 1: Notion layer + environment setup

### Task 1: Add environment variable

**Files:**
- Modify: `.env`
- Modify: `.env.example`

- [ ] **Step 1: Add LINKEDIN_CONTENT_DB_ID to .env**

Open `/Users/anton/job_tracker/.env` and append:
```
LINKEDIN_CONTENT_DB_ID=9836eeef5b1a474b9d9759110f5b9988
```

- [ ] **Step 2: Add placeholder to .env.example**

Open `/Users/anton/job_tracker/.env.example` and append:
```
LINKEDIN_CONTENT_DB_ID=your_linkedin_content_db_id
```

- [ ] **Step 3: Add pytest to requirements.txt**

Open `/Users/anton/job_tracker/requirements.txt` and append:
```
pytest>=8.0.0
```

- [ ] **Step 4: Commit**

```bash
git add .env.example requirements.txt
git commit -m "chore: add LINKEDIN_CONTENT_DB_ID env var and pytest dependency"
```

(Do NOT commit `.env` — it contains real secrets.)

---

### Task 2: Notion helper functions (shared by both scripts)

These pure functions handle Notion API calls. They live directly in each script (not in common.py — they are specific to the content DB).

**Files:**
- Create: `test_linkedin_content_notion.py`
- (Functions will be implemented in Tasks 3 and 5 — tested here first)

- [ ] **Step 1: Write failing tests for Notion query function**

Create `/Users/anton/job_tracker/test_linkedin_content_notion.py`:

```python
"""Unit tests for Notion helper logic used by publisher and analytics."""
import pytest
from unittest.mock import patch, MagicMock
from datetime import date


# ── Tests for get_scheduled_posts ─────────────────────────────────────────────

def test_get_scheduled_posts_filters_by_status_and_date():
    """Only returns posts with status=Запланирован and scheduled date <= today."""
    from linkedin_publisher import get_scheduled_posts

    mock_page = {
        "id": "abc-123",
        "properties": {
            "Заголовок": {"title": [{"plain_text": "Test Post"}]},
            "Текст поста": {"rich_text": [{"plain_text": "Hello LinkedIn"}]},
            "Статус": {"select": {"name": "Запланирован"}},
            "Дата когда нужно опубликовать ": {"date": {"start": "2026-03-15"}},
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [mock_page], "has_more": False}

    with patch("linkedin_publisher.requests.post", return_value=mock_response):
        with patch("linkedin_publisher.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 15)
            mock_date.fromisoformat = date.fromisoformat
            posts = get_scheduled_posts()

    assert len(posts) == 1
    assert posts[0]["id"] == "abc-123"
    assert posts[0]["text"] == "Hello LinkedIn"
    assert posts[0]["title"] == "Test Post"


def test_get_scheduled_posts_skips_future_date():
    """Post scheduled for tomorrow is not returned today."""
    from linkedin_publisher import get_scheduled_posts

    mock_page = {
        "id": "future-1",
        "properties": {
            "Заголовок": {"title": [{"plain_text": "Future"}]},
            "Текст поста": {"rich_text": [{"plain_text": "Not yet"}]},
            "Статус": {"select": {"name": "Запланирован"}},
            "Дата когда нужно опубликовать ": {"date": {"start": "2026-03-16"}},
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [mock_page], "has_more": False}

    with patch("linkedin_publisher.requests.post", return_value=mock_response):
        with patch("linkedin_publisher.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 15)
            mock_date.fromisoformat = date.fromisoformat
            posts = get_scheduled_posts()

    assert len(posts) == 0


def test_get_scheduled_posts_skips_missing_date():
    """Post with no scheduled date is skipped."""
    from linkedin_publisher import get_scheduled_posts

    mock_page = {
        "id": "no-date-1",
        "properties": {
            "Заголовок": {"title": [{"plain_text": "No date"}]},
            "Текст поста": {"rich_text": [{"plain_text": "text"}]},
            "Статус": {"select": {"name": "Запланирован"}},
            "Дата когда нужно опубликовать ": {"date": None},
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [mock_page], "has_more": False}

    with patch("linkedin_publisher.requests.post", return_value=mock_response):
        with patch("linkedin_publisher.date") as mock_date:
            mock_date.today.return_value = date(2026, 3, 15)
            mock_date.fromisoformat = date.fromisoformat
            posts = get_scheduled_posts()

    assert len(posts) == 0


# ── Tests for extract_text ─────────────────────────────────────────────────────

def test_extract_text_concatenates_rich_text_items():
    """rich_text array with multiple items is concatenated into one string."""
    from linkedin_publisher import extract_text

    rich_text = [
        {"plain_text": "Hello "},
        {"plain_text": "LinkedIn"},
        {"plain_text": "!"},
    ]
    assert extract_text(rich_text) == "Hello LinkedIn!"


def test_extract_text_empty_returns_empty_string():
    from linkedin_publisher import extract_text
    assert extract_text([]) == ""


# ── Tests for validate_post_text ──────────────────────────────────────────────

def test_validate_post_text_ok_under_3000():
    from linkedin_publisher import validate_post_text
    ok, err = validate_post_text("A" * 2999)
    assert ok is True
    assert err is None


def test_validate_post_text_fails_over_3000():
    from linkedin_publisher import validate_post_text
    ok, err = validate_post_text("A" * 3001)
    assert ok is False
    assert "3000" in err


def test_validate_post_text_fails_empty():
    from linkedin_publisher import validate_post_text
    ok, err = validate_post_text("")
    assert ok is False


# ── Tests for update_notion_page ──────────────────────────────────────────────

def test_update_notion_published_sends_correct_payload():
    from linkedin_publisher import update_notion_published

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("linkedin_publisher.requests.patch", return_value=mock_response) as mock_patch:
        result = update_notion_published(
            page_id="abc-123",
            publish_date="2026-03-15",
            post_url="https://www.linkedin.com/feed/update/urn:li:activity:123/",
        )

    assert result is True
    payload = mock_patch.call_args.kwargs["json"]
    props = payload["properties"]
    assert props["Статус"]["select"]["name"] == "Опубликован"
    assert props["Дата публикации"]["date"]["start"] == "2026-03-15"
    assert props["Ссылка"]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:123/"


def test_update_notion_error_writes_to_vyvody():
    from linkedin_publisher import update_notion_error

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("linkedin_publisher.requests.patch", return_value=mock_response) as mock_patch:
        update_notion_error(page_id="abc-123", reason="Текст превышает 3000 символов")

    payload = mock_patch.call_args.kwargs["json"]
    props = payload["properties"]
    assert props["Статус"]["select"]["name"] == "Ошибка"
    vyvody_text = props["Выводы"]["rich_text"][0]["text"]["content"]
    assert "3000" in vyvody_text


def test_update_notion_url_missing_marks_published_with_note():
    from linkedin_publisher import update_notion_url_missing

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("linkedin_publisher.requests.patch", return_value=mock_response) as mock_patch:
        update_notion_url_missing(page_id="abc-123", publish_date="2026-03-15")

    payload = mock_patch.call_args.kwargs["json"]
    props = payload["properties"]
    assert props["Статус"]["select"]["name"] == "Опубликован"
    assert props["Дата публикации"]["date"]["start"] == "2026-03-15"
    vyvody = props["Выводы"]["rich_text"][0]["text"]["content"]
    assert "URL" in vyvody


def test_validate_post_text_exactly_3000_is_ok():
    from linkedin_publisher import validate_post_text
    ok, err = validate_post_text("A" * 3000)
    assert ok is True
    assert err is None


# ── Tests for get_published_posts (analytics) ─────────────────────────────────

def test_get_published_posts_returns_posts_with_url():
    from linkedin_analytics import get_published_posts

    mock_page = {
        "id": "pub-1",
        "properties": {
            "Заголовок": {"title": [{"plain_text": "Published Post"}]},
            "Ссылка": {"url": "https://www.linkedin.com/feed/update/urn:li:activity:999/"},
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [mock_page], "has_more": False}

    with patch("linkedin_analytics.requests.post", return_value=mock_response):
        posts = get_published_posts()

    assert len(posts) == 1
    assert posts[0]["url"] == "https://www.linkedin.com/feed/update/urn:li:activity:999/"


def test_get_published_posts_skips_empty_url():
    from linkedin_analytics import get_published_posts

    mock_page = {
        "id": "pub-2",
        "properties": {
            "Заголовок": {"title": [{"plain_text": "No URL"}]},
            "Ссылка": {"url": None},
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"results": [mock_page], "has_more": False}

    with patch("linkedin_analytics.requests.post", return_value=mock_response):
        posts = get_published_posts()

    assert len(posts) == 0
```

- [ ] **Step 2: Run tests — expect ImportError (modules don't exist yet)**

```bash
cd /Users/anton/job_tracker && python3 -m pytest test_linkedin_content_notion.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'get_scheduled_posts' from 'linkedin_publisher'`

- [ ] **Step 3: Commit test file**

```bash
git add test_linkedin_content_notion.py
git commit -m "test: add unit tests for linkedin publisher/analytics notion layer"
```

---

## Chunk 2: linkedin_publisher.py

### Task 3: Implement linkedin_publisher.py

**Files:**
- Create: `linkedin_publisher.py`

- [ ] **Step 1: Create linkedin_publisher.py with Notion layer functions**

Create `/Users/anton/job_tracker/linkedin_publisher.py`:

```python
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
        "Ссылка":           {"url": post_url if post_url else None},
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
    Navigate to author's recent activity and grab the URL of the first post.
    Returns URL string or None if not found.
    """
    try:
        page.goto(
            "https://www.linkedin.com/in/me/recent-activity/all/",
            wait_until="domcontentloaded",
            timeout=20000,
        )
        page.wait_for_timeout(4000)

        # Find first post link
        link = page.query_selector("a[href*='activity']")
        if link:
            href = link.get_attribute("href") or ""
            if "activity" in href:
                # Normalise to full URL
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
```

- [ ] **Step 2: Run unit tests — expect them to pass for Notion layer**

```bash
cd /Users/anton/job_tracker && python3 -m pytest test_linkedin_content_notion.py -v -k "not analytics"
```

Expected: all publisher tests PASS (get_scheduled_posts, extract_text, validate_post_text, update_notion_*)

- [ ] **Step 3: Verify script syntax**

```bash
cd /Users/anton/job_tracker && python3 -c "import linkedin_publisher; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add linkedin_publisher.py
git commit -m "feat: linkedin_publisher — publish scheduled posts from Notion"
```

---

## Chunk 3: linkedin_analytics.py

### Task 4: Implement linkedin_analytics.py

**Files:**
- Create: `linkedin_analytics.py`

- [ ] **Step 1: Create linkedin_analytics.py**

Create `/Users/anton/job_tracker/linkedin_analytics.py`:

```python
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
```

- [ ] **Step 2: Run all unit tests**

```bash
cd /Users/anton/job_tracker && python3 -m pytest test_linkedin_content_notion.py -v
```

Expected: all tests PASS (both publisher and analytics sections)

- [ ] **Step 3: Verify script syntax**

```bash
cd /Users/anton/job_tracker && python3 -c "import linkedin_analytics; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add linkedin_analytics.py
git commit -m "feat: linkedin_analytics — collect post metrics into Notion"
```

---

## Chunk 4: Manual integration test

### Task 5: End-to-end test with the real test post

- [ ] **Step 1: Set the test post to "Запланирован" in Notion**

In Notion DB "✍️ LinkedIn — Посты":
- Open the Duolingo post
- Set `Статус` → `Запланирован`
- Set `Дата когда нужно опубликовать ` → today's date (2026-03-15)

- [ ] **Step 2: Run publisher**

```bash
cd /Users/anton/job_tracker && python3 linkedin_publisher.py
```

Expected output:
```
HH:MM:SS [INFO] 📝 LinkedIn Publisher — публикация по расписанию
HH:MM:SS [INFO] 📋 Найдено постов для публикации: 1
HH:MM:SS [INFO] ✅ Авторизация OK
HH:MM:SS [INFO] 📌 «Как Duolingo...»
HH:MM:SS [INFO]    ✅ Пост опубликован
HH:MM:SS [INFO]    🔗 https://www.linkedin.com/feed/update/...
HH:MM:SS [INFO] ✅ Готово
```

- [ ] **Step 3: Verify in Notion**

Check that the Duolingo post record now shows:
- `Статус` = Опубликован
- `Дата публикации` = 2026-03-15
- `Ссылка` = LinkedIn post URL

- [ ] **Step 4: Run analytics immediately after publishing (data will be 0 or None)**

```bash
cd /Users/anton/job_tracker && python3 linkedin_analytics.py
```

**Note:** Running analytics immediately after publishing is normal — LinkedIn needs time to count metrics.
The expected output for a brand-new post is:
```
HH:MM:SS [INFO] 📊 LinkedIn Analytics — сбор метрик
HH:MM:SS [INFO] 📋 Постов для обновления аналитики: 1
HH:MM:SS [INFO] ✅ Авторизация OK
HH:MM:SS [INFO] 🔍 «Как Duolingo...»
HH:MM:SS [INFO]    URL: https://www.linkedin.com/feed/update/...
HH:MM:SS [INFO]    📈 Реакции=None Комменты=None Репосты=None Просмотры=None
HH:MM:SS [INFO]    ⏭  Notion не обновлён (нет данных)
HH:MM:SS [INFO] ✅ Готово
```
This is **correct** — it means the script found and opened the post but metrics are not yet rendered.
Run again after 24-48 hours for real data.

**If you need to reset a failed test:** in Notion, manually set `Статус` back to `Черновик` or `Запланирован`, clear `Дата публикации` and `Ссылка`.

- [ ] **Step 5: Commit**

First verify that `*.log` files are in `.gitignore` (check `/Users/anton/job_tracker/.gitignore`).
If not present, add:
```
*.log
```
Then commit:
```bash
git add .gitignore
git commit -m "chore: gitignore log files"
```

---

## Quick Reference

```bash
# Публикация постов (запускай вручную по расписанию)
python3 linkedin_publisher.py

# Сбор аналитики (запускай через 24-48ч после публикации)
python3 linkedin_analytics.py

# Если сессия протухла
python3 linkedin_login.py
```

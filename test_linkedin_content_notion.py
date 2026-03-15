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


def test_validate_post_text_exactly_3000_is_ok():
    from linkedin_publisher import validate_post_text
    ok, err = validate_post_text("A" * 3000)
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

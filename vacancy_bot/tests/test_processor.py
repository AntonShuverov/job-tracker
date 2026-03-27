import pytest
from unittest.mock import patch
from processor import parse_and_save


async def test_saves_structured_vacancy_when_qwen_succeeds():
    qwen_result = {
        "is_vacancy": True,
        "title": "Product Manager",
        "company": "Acme",
        "schedule": "Удалёнка",
        "location": "Москва",
        "salary": "300k",
        "email": None,
        "tg_contact": None,
        "linkedin_url": None,
        "vacancy_url": "https://example.com/job",
        "notes": "PM role requirements",
    }
    with patch("processor.call_qwen", return_value=qwen_result), \
         patch("processor.create_notion_page", return_value=True) as mock_notion:
        result = await parse_and_save("Some vacancy text", "test_channel", "https://t.me/test/1")
    assert result is True
    mock_notion.assert_called_once()
    vacancy_arg = mock_notion.call_args[0][0]
    assert vacancy_arg["title"] == "Product Manager"


async def test_saves_raw_text_when_qwen_returns_not_vacancy():
    with patch("processor.call_qwen", return_value={"is_vacancy": False}), \
         patch("processor.create_notion_page", return_value=True) as mock_notion:
        result = await parse_and_save("Some random text", "channel", "https://t.me/test/2")
    assert result is True
    vacancy_arg = mock_notion.call_args[0][0]
    assert "Some random text" in vacancy_arg["notes"]


async def test_returns_false_on_notion_failure():
    with patch("processor.call_qwen", return_value={"is_vacancy": False}), \
         patch("processor.create_notion_page", return_value=False):
        result = await parse_and_save("text", "channel", "link")
    assert result is False

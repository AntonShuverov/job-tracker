"""Qwen parsing + Notion saving for vacancy bot."""

import os
import re
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("vacancy_bot")

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

SCHEDULE_MAP = {
    "офис": "Офис", "office": "Офис", "fullDay": "Офис",
    "удалёнка": "Удалёнка", "удаленка": "Удалёнка", "remote": "Удалёнка",
    "гибрид": "Гибрид", "hybrid": "Гибрид", "flexible": "Гибрид",
    "flyInFlyOut": "Офис", "shift": "Офис",
}
VALID_SCHEDULES = {"Офис", "Удалёнка", "Гибрид", "Не указано"}

PARSE_PROMPT = """Ты парсишь вакансии из Telegram-канала.

Если это НЕ вакансия — верни: {"is_vacancy": false}

Если вакансия:
{
  "is_vacancy": true,
  "title": "Точное название должности",
  "company": "Компания",
  "schedule": "Офис / Удалёнка / Гибрид / Не указано",
  "location": "ВСЕ города через запятую",
  "salary": "Зарплата или null",
  "email": "Email или null",
  "tg_contact": "@username того кто принимает резюме или null",
  "linkedin_url": "Ссылка содержащая linkedin.com или null",
  "vacancy_url": "career page, hh.ru, google form (НЕ linkedin, НЕ t.me) или null",
  "notes": "Требования и обязанности кратко (2-3 предложения)"
}
Верни ТОЛЬКО JSON."""


def call_qwen(prompt: str) -> dict | None:
    try:
        resp = requests.post(
            QWEN_API_URL,
            headers={
                "Authorization": f"Bearer {QWEN_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": QWEN_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"Qwen {resp.status_code}")
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.error(f"Qwen: {e}")
    return None


def create_notion_page(vacancy: dict, channel: str, tg_link: str) -> bool:
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    props = {
        "Должность": {"title": [{"text": {"content": vacancy.get("title", "?")[:100]}}]},
        "Статус": {"select": {"name": "Новая"}},
        "Источник": {"select": {"name": "Telegram"}},
        "ТГ-канал": {"rich_text": [{"text": {"content": channel[:100]}}]},
    }
    if tg_link:
        props["Пост в ТГ"] = {"url": tg_link}
        props["Ссылка на вакансию"] = {"url": tg_link}
    if vacancy.get("vacancy_url"):
        props["Ссылка на вакансию"] = {"url": vacancy["vacancy_url"][:200]}
    if vacancy.get("linkedin_url"):
        props["LinkedIn"] = {"url": vacancy["linkedin_url"][:200]}
    if vacancy.get("tg_contact"):
        props["Контакт ТГ"] = {"rich_text": [{"text": {"content": vacancy["tg_contact"][:100]}}]}
    if vacancy.get("email"):
        props["Email"] = {"rich_text": [{"text": {"content": vacancy["email"][:100]}}]}
    if vacancy.get("company"):
        props["Компания"] = {"rich_text": [{"text": {"content": vacancy["company"][:100]}}]}
    if vacancy.get("schedule"):
        s = SCHEDULE_MAP.get(vacancy["schedule"].lower(), vacancy["schedule"])
        if s in VALID_SCHEDULES:
            props["Формат работы"] = {"select": {"name": s}}
    if vacancy.get("location"):
        props["Локация"] = {"rich_text": [{"text": {"content": vacancy["location"][:100]}}]}
    if vacancy.get("salary"):
        props["Зарплата"] = {"rich_text": [{"text": {"content": vacancy["salary"][:100]}}]}
    if vacancy.get("notes"):
        props["Заметки"] = {"rich_text": [{"text": {"content": vacancy["notes"][:500]}}]}
    try:
        r = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props},
            timeout=30,
        )
        if r.status_code == 200:
            logger.info(f"Notion saved: {vacancy.get('title','?')} @ {vacancy.get('company','?')}")
            return True
        logger.error(f"Notion {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"Notion: {e}")
    return False


async def parse_and_save(text: str, channel: str, tg_link: str) -> bool:
    """Parse text with Qwen and save to Notion. Always saves, even if not detected as vacancy."""
    data = call_qwen(f"{PARSE_PROMPT}\n\nТекст поста из Telegram:\n\n{text}")
    if data and data.get("is_vacancy"):
        vacancy = data
    else:
        vacancy = {
            "title": "Вакансия (без разбора)",
            "notes": text[:500],
        }
    return create_notion_page(vacancy, channel, tg_link)

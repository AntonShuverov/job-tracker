"""Shared utilities for all job tracker parsers."""

import os
import re
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

RELEVANCE_MAP = {
    "высокая": "🔥 Высокая",
    "средняя": "👍 Средняя",
    "низкая": "🤷 Низкая",
}

SCHEDULE_MAP = {
    "офис": "Офис", "office": "Офис", "fullDay": "Офис",
    "удалёнка": "Удалёнка", "удаленка": "Удалёнка", "remote": "Удалёнка",
    "гибрид": "Гибрид", "hybrid": "Гибрид", "flexible": "Гибрид",
    "flyInFlyOut": "Офис", "shift": "Офис",
}
VALID_SCHEDULES = {"Офис", "Удалёнка", "Гибрид", "Не указано"}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESUME_PATH = os.path.join(_BASE_DIR, "resume.txt")

logger = logging.getLogger("job_tracker")


def load_resume() -> str:
    if not os.path.exists(RESUME_PATH):
        logger.warning("resume.txt not found")
        return ""
    with open(RESUME_PATH, encoding="utf-8") as f:
        return f.read()


def get_notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def normalize_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].split("#")[0].rstrip("/").lower()


def call_qwen(prompt: str, max_tokens: int = 800) -> dict | None:
    try:
        resp = requests.post(
            QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"Qwen API {resp.status_code}")
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.error(f"Qwen: {e}")
    return None

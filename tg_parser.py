"""
Job Tracker v1 — Парсинг вакансий из Telegram-каналов → Notion
Использует Telethon для чтения каналов и Qwen AI для разбора сообщений.
"""

import os
import re
import json
import asyncio
import logging
from typing import Optional

from telethon import TelegramClient, events
import requests

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"

NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

TG_CHANNELS = [ch.strip() for ch in os.getenv("TG_CHANNELS", "").split(",") if ch.strip()]
INITIAL_MESSAGES_LIMIT = int(os.getenv("INITIAL_MESSAGES_LIMIT", "50"))
MODE = os.getenv("MODE", "batch")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("job_tracker")

PARSE_PROMPT = """Проанализируй текст сообщения из Telegram-канала и определи, содержит ли оно вакансию.

Если это НЕ вакансия — верни: {"is_vacancy": false}

Если это вакансия, извлеки данные и верни JSON:
{
  "is_vacancy": true,
  "title": "Название должности",
  "company": "Компания или null",
  "schedule": "Один из: Офис / Удалёнка / Гибрид / Не указано",
  "location": "Город/страна или null",
  "salary": "Зарплата или null",
  "contact": "Email, @username, ссылка или null",
  "link": "Ссылка на вакансию или null",
  "notes": "Ключевые требования кратко или null"
}
Верни ТОЛЬКО JSON, без пояснений."""


def parse_vacancy_with_ai(text):
    if not text or len(text.strip()) < 30:
        return None
    try:
        resp = requests.post(QWEN_API_URL, headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"}, json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": f"{PARSE_PROMPT}\n\nТекст:\n\n{text}"}], "max_tokens": 500}, timeout=30)
        if resp.status_code != 200:
            logger.error(f"Qwen API {resp.status_code}: {resp.text[:200]}")
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return data if data.get("is_vacancy") else None
    except Exception as e:
        logger.error(f"Qwen: {e}")
    return None


NOTION_HEADERS = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
SCHEDULE_MAP = {"офис": "Офис", "office": "Офис", "удалёнка": "Удалёнка", "удаленка": "Удалёнка", "remote": "Удалёнка", "гибрид": "Гибрид", "hybrid": "Гибрид", "не указано": "Не указано"}
VALID_SCHEDULES = {"Офис", "Удалёнка", "Гибрид", "Не указано"}


def create_notion_page(vacancy, channel_name, tg_link=""):
    props = {
        "Должность": {"title": [{"text": {"content": vacancy.get("title", "?")[:100]}}]},
        "Статус": {"select": {"name": "Новая"}},
        "Источник": {"select": {"name": "Telegram"}},
        "ТГ-канал": {"rich_text": [{"text": {"content": channel_name[:100]}}]},
    }
    link = vacancy.get("link")
    if link:
        props["Ссылка на вакансию"] = {"url": link[:200]}
    elif tg_link:
        props["Ссылка на вакансию"] = {"url": tg_link}
    if vacancy.get("company"):
        props["Компания"] = {"rich_text": [{"text": {"content": vacancy["company"][:100]}}]}
    if vacancy.get("schedule"):
        sched = SCHEDULE_MAP.get(vacancy["schedule"].lower(), vacancy["schedule"])
        if sched in VALID_SCHEDULES:
            props["Формат работы"] = {"select": {"name": sched}}
    if vacancy.get("location"):
        props["Локация"] = {"rich_text": [{"text": {"content": vacancy["location"][:100]}}]}
    if vacancy.get("salary"):
        props["Зарплата"] = {"rich_text": [{"text": {"content": vacancy["salary"][:100]}}]}
    contact = vacancy.get("contact")
    if contact and contact.startswith("http"):
        props["Ссылка на HR"] = {"url": contact[:200]}
    elif contact:
        vacancy["notes"] = f"Контакт: {contact}\n{vacancy.get('notes') or ''}".strip()
    if vacancy.get("notes"):
        props["Заметки"] = {"rich_text": [{"text": {"content": vacancy["notes"][:500]}}]}
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        if resp.status_code == 200:
            logger.info(f"✅ {vacancy.get('title', '?')} @ {vacancy.get('company', '?')}")
            return True
        logger.error(f"❌ Notion {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Notion: {e}")
    return False


def check_duplicate(title):
    try:
        resp = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query", headers=NOTION_HEADERS, json={"filter": {"property": "Должность", "title": {"equals": title}}, "page_size": 1}, timeout=15)
        if resp.status_code == 200:
            return len(resp.json().get("results", [])) > 0
    except Exception:
        pass
    return False


def make_tg_link(channel_username, message_id):
    return f"https://t.me/{channel_username.lstrip('@').lstrip('/')}/{message_id}"


async def process_message(text, channel_name, tg_link=""):
    vacancy = parse_vacancy_with_ai(text)
    if not vacancy:
        return False
    title = vacancy.get("title", "")
    if title and check_duplicate(title):
        logger.info(f"⏭️  Дубликат: {title}")
        return False
    return create_notion_page(vacancy, channel_name, tg_link)


async def batch_parse(client):
    total_msgs, total_added = 0, 0
    for channel_name in TG_CHANNELS:
        logger.info(f"\n📡 Канал: {channel_name}")
        try:
            entity = await client.get_entity(channel_name)
            username = getattr(entity, "username", channel_name)
            messages = await client.get_messages(entity, limit=INITIAL_MESSAGES_LIMIT)
            for msg in messages:
                if not msg.text:
                    continue
                total_msgs += 1
                tg_link = make_tg_link(username, msg.id)
                if await process_message(msg.text, channel_name, tg_link):
                    total_added += 1
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ {channel_name}: {e}")
    logger.info(f"\n{'='*50}\n📊 {total_msgs} сообщений → {total_added} вакансий\n{'='*50}")


async def live_monitor(client):
    channels, usernames = [], {}
    for ch in TG_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            channels.append(entity)
            usernames[entity.id] = getattr(entity, "username", ch)
            logger.info(f"📡 Подключен: {ch}")
        except Exception as e:
            logger.error(f"❌ {ch}: {e}")
    if not channels:
        logger.error("Нет каналов!")
        return

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        if not event.text:
            return
        chat = await event.get_chat()
        username = usernames.get(chat.id, getattr(chat, "username", "unknown"))
        tg_link = make_tg_link(username, event.id)
        logger.info(f"📩 Новое в {username}")
        await process_message(event.text, username, tg_link)

    logger.info("\n🔴 LIVE-мониторинг (Ctrl+C для выхода)")
    await client.run_until_disconnected()


async def main():
    required = {"TELEGRAM_API_ID": TELEGRAM_API_ID, "TELEGRAM_API_HASH": TELEGRAM_API_HASH, "QWEN_API_KEY": QWEN_API_KEY, "NOTION_TOKEN": NOTION_TOKEN, "NOTION_DATABASE_ID": NOTION_DATABASE_ID}
    missing = [k for k, v in required.items() if not v]
    if not TG_CHANNELS:
        missing.append("TG_CHANNELS")
    if missing:
        logger.error("❌ Не заданы: " + ", ".join(missing))
        logger.info("💡 Заполни .env (см. .env.example)")
        return
    client = TelegramClient("job_tracker_session", TELEGRAM_API_ID, TELEGRAM_API_HASH)
    async with client:
        me = await client.get_me()
        logger.info(f"✅ Telegram: {me.first_name} (@{me.username})")
        if MODE == "live":
            await live_monitor(client)
        else:
            await batch_parse(client)

if __name__ == "__main__":
    asyncio.run(main())

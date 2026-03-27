# Vacancy Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Telegram bot that intercepts all messages from monitored channels, shows them to the user with Save/Skip buttons, and on Save processes with Qwen and saves to Notion.

**Architecture:** Three concurrent async tasks in one process: Telethon listener writes channel messages to SQLite, SQLite poller sends pending messages via bot, aiogram dispatcher handles button presses. SQLite acts as a reliable buffer so no messages are lost on restart.

**Tech Stack:** Python 3.11+, Telethon 1.x, aiogram 3.x, aiosqlite, requests, python-dotenv, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-27-vacancy-bot-design.md`

---

## File Map

```
vacancy_bot/
├── vacancy_bot.py       # Entry point: asyncio.gather(listener, poller, dispatcher)
├── listener.py          # Telethon: watches TG_CHANNELS → writes to SQLite
├── bot.py               # aiogram: poller sends messages, handles Save/Skip buttons
├── db.py                # SQLite CRUD for message queue
├── processor.py         # Qwen parsing + Notion page creation
├── conftest.py          # pytest sys.path fix + asyncio_mode
├── tests/
│   ├── test_db.py
│   └── test_processor.py
├── .env                 # (not committed)
├── .env.example
└── requirements.txt
```

---

## Task 1: Project scaffold + db.py

**Files:**
- Create: `vacancy_bot/db.py`
- Create: `vacancy_bot/requirements.txt`
- Create: `vacancy_bot/.env.example`
- Create: `vacancy_bot/conftest.py`
- Create: `vacancy_bot/tests/__init__.py`
- Test: `vacancy_bot/tests/test_db.py`

- [ ] **Step 1: Create directories and empty files**

```bash
mkdir -p vacancy_bot/tests
touch vacancy_bot/__init__.py vacancy_bot/tests/__init__.py
```

- [ ] **Step 2: Create `vacancy_bot/requirements.txt`**

```
telethon>=1.36
aiogram>=3.13
aiosqlite>=0.20
requests>=2.31
python-dotenv>=1.0
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 3: Create `vacancy_bot/.env.example`**

```env
# Telethon (те же данные что в основном проекте)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=

# Telegram Bot — создать через @BotFather
BOT_TOKEN=

# Твой Telegram user_id — узнать через @userinfobot
YOUR_CHAT_ID=

# Каналы через запятую (те же что в TG_CHANNELS основного проекта)
TG_CHANNELS=

# Qwen
QWEN_API_KEY=
QWEN_MODEL=qwen-turbo

# Notion
NOTION_TOKEN=
NOTION_DATABASE_ID=
```

- [ ] **Step 4: Create `vacancy_bot/conftest.py`**

```python
import sys
import os

# Allow imports from vacancy_bot/ without package prefix
sys.path.insert(0, os.path.dirname(__file__))
```

Also create `vacancy_bot/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 5: Write failing tests in `vacancy_bot/tests/test_db.py`**

```python
import pytest
import aiosqlite
from db import init_db, add_message, get_pending, set_sent, set_saved, set_skipped, get_message


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


async def test_init_creates_table(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        result = await cursor.fetchone()
    assert result is not None


async def test_add_and_get_pending(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "test_channel", "Vacancy text", "https://t.me/test/1")
    pending = await get_pending(db_path)
    assert len(pending) == 1
    assert pending[0]["id"] == msg_id
    assert pending[0]["channel"] == "test_channel"
    assert pending[0]["text"] == "Vacancy text"
    assert pending[0]["status"] == "pending"


async def test_set_sent(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "ch", "text", "link")
    await set_sent(db_path, msg_id, bot_message_id=999)
    msg = await get_message(db_path, msg_id)
    assert msg["status"] == "sent"
    assert msg["bot_message_id"] == 999


async def test_set_saved(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "ch", "text", "link")
    await set_saved(db_path, msg_id)
    msg = await get_message(db_path, msg_id)
    assert msg["status"] == "saved"


async def test_set_skipped(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "ch", "text", "link")
    await set_skipped(db_path, msg_id)
    msg = await get_message(db_path, msg_id)
    assert msg["status"] == "skipped"


async def test_get_pending_excludes_sent(db_path):
    await init_db(db_path)
    id1 = await add_message(db_path, "ch", "text1", "link1")
    id2 = await add_message(db_path, "ch", "text2", "link2")
    await set_sent(db_path, id2, bot_message_id=1)
    pending = await get_pending(db_path)
    assert len(pending) == 1
    assert pending[0]["id"] == id1
```

- [ ] **Step 6: Run tests to confirm they fail**

```bash
cd vacancy_bot
pip install -r requirements.txt
python -m pytest tests/test_db.py -v
```
Expected: `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 7: Create `vacancy_bot/db.py`**

```python
"""SQLite queue for vacancy messages."""

import aiosqlite
from datetime import datetime, timezone

DB_PATH = "messages.db"


async def init_db(db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel         TEXT    NOT NULL,
                text            TEXT    NOT NULL,
                tg_link         TEXT,
                status          TEXT    NOT NULL DEFAULT 'pending',
                bot_message_id  INTEGER,
                created_at      TEXT    NOT NULL
            )
        """)
        await conn.commit()


async def add_message(db_path: str, channel: str, text: str, tg_link: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "INSERT INTO messages (channel, text, tg_link, created_at) VALUES (?, ?, ?, ?)",
            (channel, text, tg_link, now),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_pending(db_path: str = DB_PATH) -> list[dict]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE status = 'pending' ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def set_sent(db_path: str, msg_id: int, bot_message_id: int) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE messages SET status = 'sent', bot_message_id = ? WHERE id = ?",
            (bot_message_id, msg_id),
        )
        await conn.commit()


async def set_saved(db_path: str, msg_id: int) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE messages SET status = 'saved' WHERE id = ?", (msg_id,)
        )
        await conn.commit()


async def set_skipped(db_path: str, msg_id: int) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE messages SET status = 'skipped' WHERE id = ?", (msg_id,)
        )
        await conn.commit()


async def get_message(db_path: str, msg_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None
```

- [ ] **Step 8: Run tests and confirm they pass**

```bash
cd vacancy_bot
python -m pytest tests/test_db.py -v
```
Expected: 6 tests PASSED

- [ ] **Step 9: Commit**

```bash
cd ..
git add vacancy_bot/
git commit -m "feat: vacancy-bot scaffold + db.py SQLite queue"
```

---

## Task 2: processor.py (Qwen + Notion)

**Files:**
- Create: `vacancy_bot/processor.py`
- Test: `vacancy_bot/tests/test_processor.py`

Reuses the Qwen prompt from `tg_parser.py`. Key differences: no PM filter, no cover letter, no relevance. If Qwen returns `is_vacancy: false` or fails, saves raw text in Заметки.

- [ ] **Step 1: Write failing tests in `vacancy_bot/tests/test_processor.py`**

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd vacancy_bot
python -m pytest tests/test_processor.py -v
```
Expected: `ModuleNotFoundError: No module named 'processor'`

- [ ] **Step 3: Create `vacancy_bot/processor.py`**

```python
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
```

- [ ] **Step 4: Run tests and confirm they pass**

```bash
cd vacancy_bot
python -m pytest tests/test_processor.py -v
```
Expected: 3 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd ..
git add vacancy_bot/processor.py vacancy_bot/tests/test_processor.py
git commit -m "feat: processor.py — Qwen parsing + Notion save"
```

---

## Task 3: listener.py (Telethon → SQLite)

**Files:**
- Create: `vacancy_bot/listener.py`

No unit tests — Telethon internals are hard to mock meaningfully. Verified manually in Task 6.

- [ ] **Step 1: Create `vacancy_bot/listener.py`**

```python
"""Telethon listener: monitors TG channels, writes all messages to SQLite."""

import os
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from db import add_message

load_dotenv()

logger = logging.getLogger("vacancy_bot")

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TG_CHANNELS = [ch.strip() for ch in os.getenv("TG_CHANNELS", "").split(",") if ch.strip()]
SESSION_FILE = "vacancy_bot_session"


def make_tg_link(username: str | None, msg_id: int) -> str:
    if not username:
        return ""
    return f"https://t.me/{str(username).lstrip('@')}/{msg_id}"


async def run_listener(db_path: str) -> None:
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.error("TELEGRAM_API_ID or TELEGRAM_API_HASH not set")
        return
    if not TG_CHANNELS:
        logger.error("TG_CHANNELS not set")
        return

    client = TelegramClient(
        SESSION_FILE, TELEGRAM_API_ID, TELEGRAM_API_HASH,
        connection_retries=10, retry_delay=5, auto_reconnect=True,
    )
    await client.start()
    me = await client.get_me()
    logger.info(f"Telethon: {me.first_name} (@{me.username})")

    channels = []
    usernames: dict[int, str | None] = {}
    for ch in TG_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            channels.append(entity)
            usernames[entity.id] = getattr(entity, "username", None)
            logger.info(f"Watching: {ch}")
        except Exception as e:
            logger.error(f"Cannot get {ch}: {e}")

    if not channels:
        logger.error("No channels resolved — check TG_CHANNELS")
        return

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        if not event.text:
            return
        chat = await event.get_chat()
        username = usernames.get(chat.id, getattr(chat, "username", None))
        tg_link = make_tg_link(username, event.id)
        channel_name = str(username or chat.id)
        try:
            await add_message(db_path, channel_name, event.text, tg_link)
            logger.info(f"Queued [{channel_name}]: {event.text[:60]}...")
        except Exception as e:
            logger.error(f"DB write error: {e}")

    logger.info(f"Live mode: watching {len(channels)} channel(s)")
    await client.run_until_disconnected()
```

- [ ] **Step 2: Commit**

```bash
cd ..
git add vacancy_bot/listener.py
git commit -m "feat: listener.py — Telethon live monitor → SQLite"
```

---

## Task 4: bot.py (aiogram + button handling)

**Files:**
- Create: `vacancy_bot/bot.py`

Two responsibilities: `run_poller` sends pending messages every 2 seconds; `setup_handlers` registers Save/Skip callback handlers.

- [ ] **Step 1: Create `vacancy_bot/bot.py`**

```python
"""aiogram bot: sends vacancy messages with Save/Skip buttons, handles responses."""

import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from db import get_pending, set_sent, set_saved, set_skipped, get_message
from processor import parse_and_save

load_dotenv()

logger = logging.getLogger("vacancy_bot")

YOUR_CHAT_ID = int(os.getenv("YOUR_CHAT_ID", "0"))
POLL_INTERVAL = 2  # seconds


class VacancyCallback(CallbackData, prefix="v"):
    action: str   # "save" or "skip"
    msg_id: int   # SQLite row id


def make_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Сохранить",
        callback_data=VacancyCallback(action="save", msg_id=msg_id),
    )
    builder.button(
        text="❌ Пропустить",
        callback_data=VacancyCallback(action="skip", msg_id=msg_id),
    )
    return builder.as_markup()


def format_message(row: dict) -> str:
    channel = row["channel"]
    tg_link = row.get("tg_link", "")
    header = f"📡 {channel}"
    if tg_link:
        header += f"\n{tg_link}"
    text = row["text"]
    if len(text) > 3800:
        text = text[:3800] + "..."
    return f"{header}\n\n{text}"


async def run_poller(db_path: str, bot: Bot) -> None:
    """Poll SQLite every POLL_INTERVAL seconds, send pending messages to user."""
    logger.info("Bot poller started")
    while True:
        try:
            pending = await get_pending(db_path)
            for row in pending:
                try:
                    sent = await bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=format_message(row),
                        reply_markup=make_keyboard(row["id"]),
                    )
                    await set_sent(db_path, row["id"], sent.message_id)
                    logger.info(f"Sent to bot: db_id={row['id']}")
                except Exception as e:
                    logger.error(f"Send error db_id={row['id']}: {e}")
        except Exception as e:
            logger.error(f"Poller error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


def setup_handlers(dp: Dispatcher, db_path: str, bot: Bot) -> None:
    """Register Save/Skip callback handlers."""

    @dp.callback_query(VacancyCallback.filter(F.action == "save"))
    async def handle_save(callback: CallbackQuery, callback_data: VacancyCallback):
        # Must answer within 30s — do it immediately before slow Qwen/Notion calls
        await callback.answer("Сохраняю...")
        row = await get_message(db_path, callback_data.msg_id)
        if not row:
            await callback.message.edit_text("Сообщение не найдено в БД")
            return
        ok = await parse_and_save(row["text"], row["channel"], row.get("tg_link", ""))
        if ok:
            await set_saved(db_path, callback_data.msg_id)
            preview = row["text"][:200] + ("..." if len(row["text"]) > 200 else "")
            await callback.message.edit_text(f"✅ Сохранено в Notion\n\n{preview}")
        else:
            # callback.answer already called — use send_message for error notification
            # Original message retains its buttons so user can retry
            await callback.bot.send_message(
                YOUR_CHAT_ID,
                "❌ Ошибка при сохранении (Qwen/Notion). Нажми Сохранить ещё раз.",
            )

    @dp.callback_query(VacancyCallback.filter(F.action == "skip"))
    async def handle_skip(callback: CallbackQuery, callback_data: VacancyCallback):
        await set_skipped(db_path, callback_data.msg_id)
        await callback.answer("Пропущено")
        await callback.message.delete()
```

- [ ] **Step 2: Commit**

```bash
cd ..
git add vacancy_bot/bot.py
git commit -m "feat: bot.py — aiogram poller + Save/Skip handlers"
```

---

## Task 5: vacancy_bot.py (entry point)

**Files:**
- Create: `vacancy_bot/vacancy_bot.py`

- [ ] **Step 1: Create `vacancy_bot/vacancy_bot.py`**

```python
"""Entry point: runs Telethon listener + aiogram bot concurrently."""

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from db import init_db
from listener import run_listener
from bot import run_poller, setup_handlers

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vacancy_bot")

DB_PATH = "messages.db"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in .env")
        return

    await init_db(DB_PATH)
    logger.info("DB ready")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    setup_handlers(dp, DB_PATH, bot)

    logger.info("Starting vacancy bot...")
    await asyncio.gather(
        run_listener(DB_PATH),
        run_poller(DB_PATH, bot),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Commit**

```bash
cd ..
git add vacancy_bot/vacancy_bot.py
git commit -m "feat: vacancy_bot.py — entry point, asyncio.gather all tasks"
```

---

## Task 6: Setup + smoke test

**Files:**
- Create: `vacancy_bot/.env` (from template, not committed)
- Modify: `.gitignore`

- [ ] **Step 1: Update `.gitignore`**

Add to the root `.gitignore`:
```
vacancy_bot/.env
vacancy_bot/messages.db
vacancy_bot/vacancy_bot_session*
```

```bash
git add .gitignore
git commit -m "chore: gitignore vacancy_bot secrets and session"
```

- [ ] **Step 2: Create `.env`**

```bash
cd vacancy_bot
cp .env.example .env
# Fill in all values:
# TELEGRAM_API_ID + TELEGRAM_API_HASH — same as main project
# BOT_TOKEN — create bot via @BotFather → /newbot
# YOUR_CHAT_ID — send any message to @userinfobot to get your id
# TG_CHANNELS — same channels as main project
# QWEN_API_KEY — same as main project
# NOTION_TOKEN + NOTION_DATABASE_ID — same as main project
```

- [ ] **Step 3: First launch (Telethon auth)**

```bash
cd vacancy_bot
python3 vacancy_bot.py
# Telethon will ask: enter phone number → enter SMS code
# Session saved to vacancy_bot_session.session
# Both listener and bot start after auth
```

- [ ] **Step 4: Smoke test**

Send a message in any of the monitored channels (or ask a contact to). Expected within 2-3 seconds:
- Message appears in your Telegram bot chat
- Two buttons: `✅ Сохранить` and `❌ Пропустить`

Press **Сохранить** → check Notion database for a new row with Статус=Новая.
Press **Пропустить** on another message → message disappears from the bot chat.

- [ ] **Step 5: Run full test suite**

```bash
cd vacancy_bot
python -m pytest tests/ -v
```
Expected: 9 tests PASSED (6 db + 3 processor)

---

## Background run (after smoke test passes)

```bash
cd vacancy_bot
nohup python3 vacancy_bot.py > bot.log 2>&1 &
tail -f bot.log
```

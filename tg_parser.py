"""
Job Tracker v1 — Парсинг вакансий из Telegram-каналов → Notion
Telethon + Qwen AI + Notion API + LinkedIn (Playwright Async) + Web Scraping
"""

import os
import re
import json
import asyncio
import logging
from typing import Optional

from telethon import TelegramClient, events
import requests
from bs4 import BeautifulSoup

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

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

# Async Playwright
_pw = None
_browser = None
_context = None

async def get_linkedin_context():
    global _pw, _browser, _context
    if _context:
        return _context
    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")
    if not os.path.exists(session_file):
        logger.warning("linkedin_session.json не найден. Запусти linkedin_login.py")
        return None
    try:
        from playwright.async_api import async_playwright
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(headless=True)
        _context = await _browser.new_context(storage_state=session_file)
        logger.info("🔗 LinkedIn сессия загружена")
        return _context
    except Exception as e:
        logger.error(f"Playwright: {e}")
        return None

async def close_linkedin():
    global _pw, _browser, _context
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()
    _pw = _browser = _context = None


async def fetch_linkedin_text(url, max_chars=3000):
    ctx = await get_linkedin_context()
    if not ctx:
        return None
    page = None
    try:
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)
        
        selectors = [
            ".feed-shared-update-v2__description",
            ".update-components-text",
            "[data-ad-preview='message']",
            ".break-words",
        ]
        text = None
        for sel in selectors:
            el = await page.query_selector(sel)
            if el:
                text = await el.inner_text()
                if len(text) > 50:
                    break
        
        if not text or len(text) < 50:
            main = await page.query_selector("main")
            if main:
                text = await main.inner_text()
            else:
                text = await page.inner_text("body")
        
        await page.close()
        return text[:max_chars] if text else None
    except Exception as e:
        logger.debug(f"LinkedIn {url[:60]}: {e}")
        if page:
            try: await page.close()
            except: pass
        return None


def fetch_page_text(url, max_chars=3000):
    if "linkedin.com" in url:
        return None
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        main = soup.find("main") or soup.find("article") or soup.find("body")
        text = main.get_text(separator="\n", strip=True) if main else ""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        clean = "\n".join(lines)
        return clean[:max_chars] if clean else None
    except Exception as e:
        logger.debug(f"Ошибка {url[:60]}: {e}")
        return None


def extract_urls(text):
    return re.findall(r'https?://[^\s<>"\'\)]+', text)


async def fetch_extra_info(urls):
    extra = []
    for url in urls[:4]:
        if any(url.lower().endswith(ext) for ext in [".jpg", ".png", ".gif", ".pdf", ".zip"]):
            continue
        if "t.me/" in url:
            continue
        
        if "linkedin.com" in url:
            logger.info(f"  🔗 LinkedIn: {url[:70]}...")
            text = await fetch_linkedin_text(url)
        else:
            logger.info(f"  🔗 Читаю: {url[:70]}...")
            text = fetch_page_text(url)
        
        if text and len(text) > 50:
            extra.append(f"[Страница {url}]:\n{text}")
    
    return "\n\n".join(extra)


PARSE_PROMPT = """Ты парсишь вакансии из Telegram-канала. Тебе дан текст поста из ТГ и содержимое страниц по ссылкам (включая LinkedIn посты и career pages).

Если это НЕ вакансия — верни: {"is_vacancy": false}

Если вакансия, извлеки МАКСИМУМ из ВСЕХ источников:
{
  "is_vacancy": true,
  "title": "Точное название должности",
  "company": "Компания",
  "schedule": "Один из: Офис / Удалёнка / Гибрид / Не указано",
  "location": "ВСЕ города через запятую",
  "salary": "Зарплата или null",
  "email": "Email для отклика или null",
  "tg_contact": "@username рекрутера/HR в Telegram или null",
  "linkedin_url": "Ссылка содержащая linkedin.com или null",
  "vacancy_url": "Ссылка на вакансию (НЕ linkedin, НЕ t.me) или null",
  "notes": "Требования и обязанности кратко (2-3 предложения)"
}

ПРАВИЛА:
1. tg_contact — @username ТОЛЬКО того кто ПРИНИМАЕТ резюме. Ищи "резюме @...", "писать @...", "отклик @..." — в тексте ТГ И в LinkedIn-посте. Игнорируй @каналов.
2. linkedin_url — ТОЧНАЯ ссылка содержащая linkedin.com.
3. vacancy_url — career page, hh.ru, google form. НЕ linkedin и НЕ t.me.
4. location — ВСЕ города через запятую.
5. notes — объедини инфу из ТГ, LinkedIn и career page.
6. Верни ТОЛЬКО JSON."""


def parse_vacancy_with_ai(text, extra_info=""):
    if not text or len(text.strip()) < 30:
        return None
    full = f"Текст поста из Telegram:\n\n{text}"
    if extra_info:
        full += f"\n\n---\nСодержимое страниц:\n\n{extra_info}"
    try:
        resp = requests.post(QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": f"{PARSE_PROMPT}\n\n{full}"}], "max_tokens": 800},
            timeout=30)
        if resp.status_code != 200:
            logger.error(f"Qwen {resp.status_code}")
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
    if tg_link:
        props["Пост в ТГ"] = {"url": tg_link}
    linkedin = vacancy.get("linkedin_url")
    if linkedin and "linkedin.com" in linkedin:
        props["LinkedIn"] = {"url": linkedin[:200]}
        if not vacancy.get("vacancy_url"):
            props["Ссылка на вакансию"] = {"url": linkedin[:200]}
    if vacancy.get("vacancy_url"):
        props["Ссылка на вакансию"] = {"url": vacancy["vacancy_url"][:200]}
    if "Ссылка на вакансию" not in props and tg_link:
        props["Ссылка на вакансию"] = {"url": tg_link}
    if vacancy.get("tg_contact"):
        props["Контакт ТГ"] = {"rich_text": [{"text": {"content": vacancy["tg_contact"][:100]}}]}
    if vacancy.get("email"):
        props["Email"] = {"rich_text": [{"text": {"content": vacancy["email"][:100]}}]}
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
    if vacancy.get("notes"):
        props["Заметки"] = {"rich_text": [{"text": {"content": vacancy["notes"][:500]}}]}
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS,
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        if resp.status_code == 200:
            logger.info(f"  ✅ {vacancy.get('title', '?')} @ {vacancy.get('company', '?')}")
            return True
        logger.error(f"  ❌ Notion {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"  ❌ Notion: {e}")
    return False


def check_duplicate(title):
    try:
        resp = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=NOTION_HEADERS,
            json={"filter": {"property": "Должность", "title": {"equals": title}}, "page_size": 1}, timeout=15)
        if resp.status_code == 200:
            return len(resp.json().get("results", [])) > 0
    except Exception:
        pass
    return False


def make_tg_link(ch, msg_id):
    return f"https://t.me/{ch.lstrip('@').lstrip('/')}/{msg_id}"


async def process_message(text, channel_name, tg_link=""):
    urls = extract_urls(text)
    extra_info = await fetch_extra_info(urls) if urls else ""
    vacancy = parse_vacancy_with_ai(text, extra_info)
    if not vacancy:
        return False
    title = vacancy.get("title", "")
    if title and check_duplicate(title):
        logger.info(f"  ⏭️  Дубликат: {title}")
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
                logger.info(f"\n📝 #{total_msgs}")
                if await process_message(msg.text, channel_name, tg_link):
                    total_added += 1
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ {channel_name}: {e}")
    logger.info(f"\n{'='*50}\n📊 {total_msgs} сообщений → {total_added} вакансий\n{'='*50}")
    await close_linkedin()


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
        un = usernames.get(chat.id, getattr(chat, "username", "unknown"))
        tg_link = make_tg_link(un, event.id)
        logger.info(f"📩 Новое в {un}")
        await process_message(event.text, un, tg_link)
    logger.info("\n🔴 LIVE-мониторинг (Ctrl+C для выхода)")
    await client.run_until_disconnected()


async def main():
    required = {"TELEGRAM_API_ID": TELEGRAM_API_ID, "TELEGRAM_API_HASH": TELEGRAM_API_HASH,
                "QWEN_API_KEY": QWEN_API_KEY, "NOTION_TOKEN": NOTION_TOKEN, "NOTION_DATABASE_ID": NOTION_DATABASE_ID}
    missing = [k for k, v in required.items() if not v]
    if not TG_CHANNELS:
        missing.append("TG_CHANNELS")
    if missing:
        logger.error("❌ Не заданы: " + ", ".join(missing))
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

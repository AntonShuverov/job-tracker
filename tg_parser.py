"""
Job Tracker v2 — Парсинг вакансий из Telegram → Notion
Telethon + Qwen AI + Notion + LinkedIn (Playwright) + Cover Letters

Исправления v2:
- Дедупликация по URL (локальное сравнение, не API filter)
- Keepalive / автореконнект Telegram клиента
- Фильтрация: только Product / Продукт вакансии
- Фильтр по году: только 2026+
- NOTION_HEADERS инициализируется после load_dotenv
"""

import os
import re
import json
import asyncio
import logging

from dotenv import load_dotenv
load_dotenv()

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
import requests
from bs4 import BeautifulSoup

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TG_CHANNELS = [ch.strip() for ch in os.getenv("TG_CHANNELS", "").split(",") if ch.strip()]
INITIAL_MESSAGES_LIMIT = int(os.getenv("INITIAL_MESSAGES_LIMIT", "50"))
MODE = os.getenv("MODE", "batch")

from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP, SCHEDULE_MAP, VALID_SCHEDULES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("job_tracker")

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

# ── Резюме ──
RESUME_TEXT = load_resume()

# ── PM фильтр ──
PM_KEYWORDS = [
    "product",
    "продукт",
    "продакт",
    "продукт-менеджер",
    "руководитель продуктового",
    "ai product",
    "cpo",
    "chief product",
]

def is_pm_vacancy(title: str) -> bool:
    if not title:
        return False
    return any(kw in title.lower() for kw in PM_KEYWORDS)


# ── Playwright (LinkedIn) ──
_pw = None
_browser = None
_context = None

async def get_linkedin_context():
    global _pw, _browser, _context
    if _context:
        return _context
    session_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")
    if not os.path.exists(session_file):
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
            ".break-words"
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
            text = await main.inner_text() if main else await page.inner_text("body")
        await page.close()
        return text[:max_chars] if text else None
    except Exception as e:
        logger.debug(f"LinkedIn {url[:60]}: {e}")
        if page:
            try:
                await page.close()
            except:
                pass
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
    except Exception:
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


# ── AI: парсинг вакансии ──

PARSE_PROMPT = """Ты парсишь вакансии из Telegram-канала. Тебе дан текст поста из ТГ и содержимое страниц по ссылкам.

Если это НЕ вакансия — верни: {"is_vacancy": false}

Если вакансия, извлеки МАКСИМУМ:
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
1. tg_contact — @username ТОЛЬКО того кто ПРИНИМАЕТ резюме.
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
    data = call_qwen(f"{PARSE_PROMPT}\n\n{full}")
    return data if data and data.get("is_vacancy") else None


# ── AI: сопроводительное + релевантность ──

COVER_PROMPT = """Тебе дано резюме кандидата и описание вакансии.

Задачи:
1. Оцени релевантность кандидата
2. Напиши короткое сопроводительное письмо

Верни JSON:
{
  "relevance": "высокая" или "средняя" или "низкая",
  "cover_letter": "Сопроводительное письмо"
}

ПРАВИЛА для письма:
- Пиши на том же языке что и вакансия
- Если вакансия на русском — "Здравствуйте!". Если на английском — "Hi!"
- Упомяни 2-3 релевантных достижения из резюме С ЦИФРАМИ
- Объясни почему кандидат подходит на эту роль
- Закончи готовностью обсудить
- Тон: профессиональный но живой, без канцелярита
- Максимум 5-6 предложений

ПРАВИЛА для релевантности:
- "высокая" — опыт напрямую совпадает
- "средняя" — частичное совпадение
- "низкая" — мало пересечений

Верни ТОЛЬКО JSON."""


def generate_cover_letter(vacancy):
    if not RESUME_TEXT:
        return None, None
    vacancy_text = (
        f"Должность: {vacancy.get('title', '')}\n"
        f"Компания: {vacancy.get('company', '')}\n"
        f"Локация: {vacancy.get('location', '')}\n"
        f"Формат: {vacancy.get('schedule', '')}\n"
        f"Зарплата: {vacancy.get('salary', '')}\n"
        f"Требования: {vacancy.get('notes', '')}"
    )
    prompt = f"{COVER_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_text}"
    data = call_qwen(prompt)
    if data:
        return data.get("cover_letter"), data.get("relevance")
    return None, None


# ── Дедупликация по URL ──

def check_duplicate_by_url(vacancy_url: str, tg_link: str) -> bool:
    normalized = normalize_url(vacancy_url)
    tg_normalized = normalize_url(tg_link)

    if not normalized and not tg_normalized:
        return False

    try:
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=get_notion_headers(),
            json={"page_size": 100},
            timeout=15
        )
        if resp.status_code != 200:
            return False

        for page in resp.json().get("results", []):
            props = page.get("properties", {})
            vac_url = normalize_url(props.get("Ссылка на вакансию", {}).get("url") or "")
            tg_url = normalize_url(props.get("Пост в ТГ", {}).get("url") or "")

            if normalized and vac_url and normalized == vac_url:
                return True
            if tg_normalized and tg_url and tg_normalized == tg_url:
                return True

    except Exception as e:
        logger.error(f"Дедупликация: {e}")

    return False


# ── Notion: запись вакансии ──

def create_notion_page(vacancy, channel_name, tg_link="", cover_letter=None, relevance=None):
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
    if cover_letter:
        props["Сопроводительное письмо"] = {"rich_text": [{"text": {"content": cover_letter[:2000]}}]}
    if relevance:
        props["Релевантность"] = {"select": {"name": RELEVANCE_MAP.get(relevance, "👍 Средняя")}}

    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props},
            timeout=30
        )
        if resp.status_code == 200:
            emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(relevance, "")
            logger.info(f"  ✅ {emoji} {vacancy.get('title', '?')} @ {vacancy.get('company', '?')}")
            return True
        logger.error(f"  ❌ Notion {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"  ❌ Notion: {e}")
    return False


def make_tg_link(ch, msg_id):
    return f"https://t.me/{ch.lstrip('@').lstrip('/')}/{msg_id}"


# ── Обработка сообщения ──

async def process_message(text, channel_name, tg_link=""):
    urls = extract_urls(text)
    extra_info = await fetch_extra_info(urls) if urls else ""

    vacancy = parse_vacancy_with_ai(text, extra_info)
    if not vacancy:
        return False

    title = vacancy.get("title", "")

    if not is_pm_vacancy(title):
        logger.info(f"  ⏭️  Не PM: «{title}» — пропускаю")
        return False

    vacancy_url = vacancy.get("vacancy_url") or vacancy.get("linkedin_url") or ""
    if check_duplicate_by_url(vacancy_url, tg_link):
        logger.info(f"  ⏭️  Дубликат: {title}")
        return False

    cover_letter, relevance = None, None
    if RESUME_TEXT:
        logger.info(f"  ✍️  Генерирую письмо...")
        cover_letter, relevance = generate_cover_letter(vacancy)

    return create_notion_page(vacancy, channel_name, tg_link, cover_letter, relevance)


# ── Keepalive ──

async def keepalive_loop(client):
    while True:
        try:
            await asyncio.sleep(300)
            await client.get_me()
            logger.debug("💓 Keepalive ping OK")
        except Exception as e:
            logger.warning(f"⚠️  Keepalive: {e}")


# ── Batch парсинг ──

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
                if msg.date.year < 2026:
                    continue
                total_msgs += 1
                tg_link = make_tg_link(username, msg.id)
                logger.info(f"\n📝 #{total_msgs}")
                if await process_message(msg.text, channel_name, tg_link):
                    total_added += 1
                await asyncio.sleep(1)
        except FloodWaitError as e:
            logger.warning(f"⏳ FloodWait {channel_name}: ждём {e.seconds}с")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"❌ {channel_name}: {e}")
    logger.info(f"\n{'='*50}\n📊 {total_msgs} сообщений → {total_added} вакансий\n{'='*50}")
    await close_linkedin()


# ── Live мониторинг ──

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
    keepalive_task = asyncio.create_task(keepalive_loop(client))
    try:
        await client.run_until_disconnected()
    finally:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass


# ── Main ──

async def main():
    required = {
        "TELEGRAM_API_ID": TELEGRAM_API_ID,
        "TELEGRAM_API_HASH": TELEGRAM_API_HASH,
        "QWEN_API_KEY": QWEN_API_KEY,
        "NOTION_TOKEN": NOTION_TOKEN,
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if not TG_CHANNELS:
        missing.append("TG_CHANNELS")
    if missing:
        logger.error("❌ Не заданы: " + ", ".join(missing))
        return

    if RESUME_TEXT:
        logger.info("📄 Резюме загружено")
    else:
        logger.warning("⚠️  resume.txt не найден — без сопроводительных")

    logger.info(f"🎯 PM фильтр: {', '.join(PM_KEYWORDS)}")

    client = TelegramClient(
        "job_tracker_session",
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH,
        connection_retries=10,
        retry_delay=5,
        auto_reconnect=True,
        request_retries=5,
    )

    async with client:
        me = await client.get_me()
        logger.info(f"✅ Telegram: {me.first_name} (@{me.username})")
        if MODE == "live":
            await live_monitor(client)
        else:
            await batch_parse(client)


if __name__ == "__main__":
    asyncio.run(main())
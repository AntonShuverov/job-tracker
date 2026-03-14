"""
tg_parser.py v3 — Парсинг вакансий из Telegram → Notion
Исправления v3:
- evacuatejobs баг: username=None → fallback на channel_id
- NOTION_HEADERS через функцию (не константа)
- Фильтр по дате: последние N дней (DATE_DAYS из env)
- INITIAL_MESSAGES_LIMIT увеличен до 200 по умолчанию
"""

import os
import re
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
import requests
from bs4 import BeautifulSoup

TELEGRAM_API_ID   = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
QWEN_API_KEY      = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL        = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL      = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
TG_CHANNELS       = [ch.strip() for ch in os.getenv("TG_CHANNELS", "").split(",") if ch.strip()]
INITIAL_MESSAGES_LIMIT = int(os.getenv("INITIAL_MESSAGES_LIMIT", "200"))
DATE_DAYS         = int(os.getenv("DATE_DAYS", "14"))   # последние N дней
MODE              = os.getenv("MODE", "batch")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("job_tracker")

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

# ── Резюме ──
RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
RESUME_TEXT = ""
if os.path.exists(RESUME_PATH):
    with open(RESUME_PATH, "r", encoding="utf-8") as f:
        RESUME_TEXT = f.read()

# ── PM-фильтр ──
PM_KEYWORDS = [
    "product manager", "product owner", "продакт", "менеджер продукта",
    "продукт-менеджер", "руководитель продукт", "head of product",
    "chief product", "cpo", "ai product", "product lead",
]

def is_pm_vacancy(title: str) -> bool:
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in PM_KEYWORDS)


# ── Notion headers — через функцию, не константа ──
def get_notion_headers():
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

SCHEDULE_MAP   = {"офис":"Офис","office":"Офис","удалёнка":"Удалёнка","удаленка":"Удалёнка",
                  "remote":"Удалёнка","гибрид":"Гибрид","hybrid":"Гибрид","не указано":"Не указано"}
VALID_SCHEDULES = {"Офис","Удалёнка","Гибрид","Не указано"}
RELEVANCE_MAP  = {"высокая":"🔥 Высокая","средняя":"👍 Средняя","низкая":"🤷 Низкая"}


# ── Playwright (LinkedIn) ──
_pw = _browser = _context = None

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
    if _browser: await _browser.close()
    if _pw: await _pw.stop()
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
        # Пробуем разные селекторы
        for sel in [".update-components-text", ".feed-shared-text", ".break-words"]:
            el = await page.query_selector(sel)
            if el:
                text = await el.inner_text()
                if len(text) > 50:
                    await page.close()
                    return text[:max_chars]
        # Fallback: весь текст страницы, режем по разделителю
        body = await page.inner_text("body")
        blocks = re.split(r'(?i)публикация в ленте', body)
        text = blocks[1] if len(blocks) > 1 else body
        await page.close()
        return text[:max_chars]
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
        for tag in soup(["script","style","nav","header","footer","aside"]):
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
        if any(url.lower().endswith(ext) for ext in [".jpg",".png",".gif",".pdf",".zip"]):
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


def call_qwen(prompt, max_tokens=800):
    try:
        resp = requests.post(QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=30)
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.error(f"Qwen: {e}")
    return None

def parse_vacancy_with_ai(text, extra_info=""):
    if not text or len(text.strip()) < 30:
        return None
    full = f"Текст поста из Telegram:\n\n{text}"
    if extra_info:
        full += f"\n\n---\nСодержимое страниц:\n\n{extra_info}"
    data = call_qwen(f"{PARSE_PROMPT}\n\n{full}")
    return data if data and data.get("is_vacancy") else None


# ── AI: сопроводительное + релевантность ──

COVER_PROMPT = """Резюме кандидата и вакансия.

Верни JSON:
{
  "relevance": "высокая" | "средняя" | "низкая",
  "cover_letter": "Письмо 5-6 предложений"
}

Правила:
- Язык вакансии (ru/en)
- Если ru — "Здравствуйте!", если en — "Hi!"
- 2-3 достижения из резюме с цифрами
- Почему подходит на роль
- Профессиональный но живой тон
Верни ТОЛЬКО JSON."""

def generate_cover_letter(vacancy):
    if not RESUME_TEXT:
        return None, None
    vt = (f"Должность: {vacancy.get('title','')}\nКомпания: {vacancy.get('company','')}\n"
          f"Локация: {vacancy.get('location','')}\nФормат: {vacancy.get('schedule','')}\n"
          f"Зарплата: {vacancy.get('salary','')}\nТребования: {vacancy.get('notes','')}")
    data = call_qwen(f"{COVER_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vt}")
    if data:
        return data.get("cover_letter"), data.get("relevance")
    return None, None


# ── Дедупликация по URL ──

def normalize_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].split("#")[0].rstrip("/").lower()

def check_duplicate_by_url(vacancy_url: str, tg_link: str) -> bool:
    nv = normalize_url(vacancy_url)
    nt = normalize_url(tg_link)
    if not nv and not nt:
        return False
    try:
        if nv:
            r = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=get_notion_headers(),
                json={"filter": {"property": "Ссылка на вакансию", "url": {"equals": nv}}, "page_size": 1},
                timeout=15)
            if r.status_code == 200 and r.json().get("results"):
                return True
        if nt:
            r = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=get_notion_headers(),
                json={"filter": {"property": "Пост в ТГ", "url": {"equals": nt}}, "page_size": 1},
                timeout=15)
            if r.status_code == 200 and r.json().get("results"):
                return True
    except Exception as e:
        logger.error(f"Дедупликация: {e}")
    return False


# ── Notion: запись ──

def create_notion_page(vacancy, channel_name, tg_link="", cover_letter=None, relevance=None):
    props = {
        "Должность": {"title": [{"text": {"content": vacancy.get("title","?")[:100]}}]},
        "Статус":    {"select": {"name": "Новая"}},
        "Источник":  {"select": {"name": "Telegram"}},
        "ТГ-канал":  {"rich_text": [{"text": {"content": channel_name[:100]}}]},
    }
    if tg_link:
        props["Пост в ТГ"] = {"url": tg_link}
    linkedin = vacancy.get("linkedin_url")
    if linkedin and "linkedin.com" in linkedin:
        props["LinkedIn"] = {"url": linkedin[:200]}
    if vacancy.get("vacancy_url"):
        props["Ссылка на вакансию"] = {"url": vacancy["vacancy_url"][:200]}
    elif linkedin and "linkedin.com" in (linkedin or ""):
        props["Ссылка на вакансию"] = {"url": linkedin[:200]}
    elif tg_link:
        props["Ссылка на вакансию"] = {"url": tg_link}
    if vacancy.get("tg_contact"): props["Контакт ТГ"] = {"rich_text": [{"text": {"content": vacancy["tg_contact"][:100]}}]}
    if vacancy.get("email"):      props["Email"]       = {"rich_text": [{"text": {"content": vacancy["email"][:100]}}]}
    if vacancy.get("company"):    props["Компания"]    = {"rich_text": [{"text": {"content": vacancy["company"][:100]}}]}
    if vacancy.get("schedule"):
        s = SCHEDULE_MAP.get(vacancy["schedule"].lower(), vacancy["schedule"])
        if s in VALID_SCHEDULES:
            props["Формат работы"] = {"select": {"name": s}}
    if vacancy.get("location"):   props["Локация"]     = {"rich_text": [{"text": {"content": vacancy["location"][:100]}}]}
    if vacancy.get("salary"):     props["Зарплата"]    = {"rich_text": [{"text": {"content": vacancy["salary"][:100]}}]}
    if vacancy.get("notes"):      props["Заметки"]     = {"rich_text": [{"text": {"content": vacancy["notes"][:500]}}]}
    if cover_letter: props["Сопроводительное письмо"] = {"rich_text": [{"text": {"content": cover_letter[:2000]}}]}
    if relevance:    props["Релевантность"] = {"select": {"name": RELEVANCE_MAP.get(relevance, "👍 Средняя")}}

    try:
        r = requests.post("https://api.notion.com/v1/pages", headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        if r.status_code == 200:
            emoji = {"высокая":"🔥","средняя":"👍","низкая":"🤷"}.get(relevance,"")
            logger.info(f"  ✅ {emoji} {vacancy.get('title','?')} @ {vacancy.get('company','?')}")
            return True
        logger.error(f"  ❌ Notion {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"  ❌ Notion: {e}")
    return False


# ── FIX: make_tg_link с защитой от None ──
def make_tg_link(ch, msg_id):
    if not ch:
        return ""
    ch = str(ch).lstrip("@").lstrip("/")
    return f"https://t.me/{ch}/{msg_id}"


# ── Обработка одного сообщения ──

async def process_message(text, channel_name, tg_link=""):
    urls = extract_urls(text)
    extra_info = await fetch_extra_info(urls) if urls else ""

    vacancy = parse_vacancy_with_ai(text, extra_info)
    if not vacancy:
        return False

    title = vacancy.get("title", "")
    if not is_pm_vacancy(title):
        logger.info(f"  ⏭️  Не PM: «{title}»")
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


# ── Batch-парсинг ──

async def batch_parse(client):
    total_msgs = total_added = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=DATE_DAYS)
    logger.info(f"📅 Фильтр: сообщения за последние {DATE_DAYS} дней (с {cutoff.strftime('%d.%m.%Y')})\n")

    for channel_name in TG_CHANNELS:
        logger.info(f"\n📡 Канал: {channel_name}")
        try:
            entity = await client.get_entity(channel_name)
            # FIX: username может быть None (приватный канал)
            username = getattr(entity, "username", None)  # None если приватный канал

            messages = await client.get_messages(entity, limit=INITIAL_MESSAGES_LIMIT)
            for msg in messages:
                if not msg.text:
                    continue
                # Фильтр по дате — пропускаем старые
                msg_date = msg.date.replace(tzinfo=timezone.utc) if msg.date.tzinfo is None else msg.date
                if msg_date < cutoff:
                    logger.info(f"  📅 Старое сообщение ({msg_date.strftime('%d.%m.%Y')}), прекращаю канал")
                    break

                total_msgs += 1
                tg_link = make_tg_link(username, msg.id)
                logger.info(f"\n📝 #{total_msgs} [{msg_date.strftime('%d.%m')}]")
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


# ── Live-мониторинг ──

async def keepalive_loop(client):
    while True:
        try:
            await asyncio.sleep(300)
            await client.get_me()
            logger.debug("💓 Keepalive OK")
        except Exception as e:
            logger.warning(f"⚠️ Keepalive: {e}")

async def live_monitor(client):
    channels, usernames = [], {}
    for ch in TG_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            channels.append(entity)
            # FIX: username может быть None
            usernames[entity.id] = getattr(entity, "username", None) or str(entity.id)
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
        un = usernames.get(chat.id, getattr(chat, "username", None) or str(chat.id))
        tg_link = make_tg_link(un, event.id)
        logger.info(f"📩 Новое в {un}")
        await process_message(event.text, un, tg_link)

    logger.info("\n🔴 LIVE-мониторинг (Ctrl+C для выхода)")
    keepalive_task = asyncio.create_task(keepalive_loop(client))
    try:
        await client.run_until_disconnected()
    finally:
        keepalive_task.cancel()
        try: await keepalive_task
        except asyncio.CancelledError: pass


# ── Main ──

async def main():
    required = {
        "TELEGRAM_API_ID": TELEGRAM_API_ID, "TELEGRAM_API_HASH": TELEGRAM_API_HASH,
        "QWEN_API_KEY": QWEN_API_KEY, "NOTION_TOKEN": os.getenv("NOTION_TOKEN",""),
        "NOTION_DATABASE_ID": NOTION_DATABASE_ID,
    }
    missing = [k for k, v in required.items() if not v]
    if not TG_CHANNELS: missing.append("TG_CHANNELS")
    if missing:
        logger.error("❌ Не заданы: " + ", ".join(missing))
        return

    logger.info("📄 Резюме загружено" if RESUME_TEXT else "⚠️  resume.txt не найден")
    logger.info(f"🎯 PM-фильтр: {len(PM_KEYWORDS)} ключевых слов")
    logger.info(f"📅 Глубина: {DATE_DAYS} дней, лимит: {INITIAL_MESSAGES_LIMIT} сообщений/канал")

    client = TelegramClient(
        "job_tracker_session", TELEGRAM_API_ID, TELEGRAM_API_HASH,
        connection_retries=10, retry_delay=5, auto_reconnect=True, request_retries=5,
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

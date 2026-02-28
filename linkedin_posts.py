"""
Job Tracker — Парсинг вакансий из LinkedIn-постов → Notion
Ищет посты где люди нанимают: "ищу продакта", "hiring PM" и т.д.
"""

import os
import re
import json
import logging
import time
import requests
from urllib.parse import quote

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("linkedin_posts")

RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
RESUME_TEXT = ""
if os.path.exists(RESUME_PATH):
    with open(RESUME_PATH, "r", encoding="utf-8") as f:
        RESUME_TEXT = f.read()

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")

# ═══════════════════════════════════════
# ПОИСКОВЫЕ ЗАПРОСЫ
# ═══════════════════════════════════════

SEARCH_QUERIES = [
    "ищу продакта",
    "ищу product manager",
    "ищем продакт менеджера",
    "вакансия product manager",
    "hiring product manager remote",
    "ищу проджект менеджера",
]

# ═══════════════════════════════════════

NOTION_HEADERS = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
RELEVANCE_MAP = {"высокая": "🔥 Высокая", "средняя": "👍 Средняя", "низкая": "🤷 Низкая"}


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


PARSE_PROMPT = """Проанализируй текст LinkedIn-поста. Это вакансия/поиск сотрудника?

Если автор НЕ ищет сотрудника (а ищет работу сам, или это не про найм) — верни: {"is_vacancy": false}

Если автор ИЩЕТ сотрудника, извлеки:
{
  "is_vacancy": true,
  "title": "Название должности",
  "company": "Компания или null",
  "schedule": "Офис / Удалёнка / Гибрид / Не указано",
  "location": "Город/страна или null",
  "salary": "Зарплата или null",
  "email": "Email для отклика или null",
  "tg_contact": "@username Telegram или null",
  "vacancy_url": "Ссылка на форму/вакансию из текста или null",
  "notes": "Ключевые требования (2-3 предложения)"
}

ВАЖНО: отличай "ищу работу" (НЕ вакансия) от "ищу сотрудника" (вакансия).
Верни ТОЛЬКО JSON."""


COVER_PROMPT = """Проанализируй вакансию и резюме.

Верни JSON:
{
  "relevance": "высокая" или "средняя" или "низкая",
  "cover_letter": "Сопроводительное письмо (5-6 предложений)"
}

Правила:
- Если вакансия на русском — "Здравствуйте!". Если на английском — "Hi!"
- 2-3 достижения из резюме С ЦИФРАМИ
- Почему кандидат подходит
- Тон: профессиональный, живой
- Максимум 5-6 предложений

Релевантность: "высокая" / "средняя" / "низкая"
ТОЛЬКО JSON."""


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


def create_notion_page(vacancy, linkedin_url, author_name="", cover_letter=None, relevance=None):
    props = {
        "Должность": {"title": [{"text": {"content": vacancy.get("title", "?")[:100]}}]},
        "Статус": {"select": {"name": "Новая"}},
        "Источник": {"select": {"name": "LinkedIn"}},
        "LinkedIn": {"url": linkedin_url[:200]},
        "Ссылка на вакансию": {"url": (vacancy.get("vacancy_url") or linkedin_url)[:200]},
    }
    if vacancy.get("company"):
        props["Компания"] = {"rich_text": [{"text": {"content": vacancy["company"][:100]}}]}
    if vacancy.get("tg_contact"):
        props["Контакт ТГ"] = {"rich_text": [{"text": {"content": vacancy["tg_contact"][:100]}}]}
    if vacancy.get("email"):
        props["Email"] = {"rich_text": [{"text": {"content": vacancy["email"][:100]}}]}
    if vacancy.get("schedule"):
        sched_map = {"офис": "Офис", "удалёнка": "Удалёнка", "удаленка": "Удалёнка", "гибрид": "Гибрид", "remote": "Удалёнка"}
        sched = sched_map.get(vacancy["schedule"].lower(), vacancy["schedule"])
        if sched in {"Офис", "Удалёнка", "Гибрид", "Не указано"}:
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
        resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS,
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        return resp.status_code == 200
    except Exception:
        return False


def main():
    total_posts = 0
    total_vacancies = 0
    total_added = 0
    seen_texts = set()

    logger.info("🔗 Парсинг LinkedIn-постов с вакансиями\n")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(storage_state=SESSION_FILE)
    page = context.new_page()

    try:
        for query in SEARCH_QUERIES:
            encoded = quote(query)
            url = f"https://www.linkedin.com/search/results/content/?keywords={encoded}&sortBy=%22date_posted%22"

            logger.info(f"\n🔍 «{query}»")
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Скроллим для подгрузки
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

            # Собираем посты и ссылки на них
            post_containers = page.query_selector_all(".occludable-update, .feed-shared-update-v2")
            logger.info(f"   Постов: {len(post_containers)}")

            for container in post_containers:
                # Текст поста
                text_el = container.query_selector(".update-components-text, .feed-shared-text")
                if not text_el:
                    continue
                post_text = text_el.inner_text().strip()

                if len(post_text) < 50:
                    continue

                # Дедупликация по первым 100 символам
                text_key = post_text[:100]
                if text_key in seen_texts:
                    continue
                seen_texts.add(text_key)
                total_posts += 1

                # Ссылка на пост
                urn_el = container.query_selector("[data-urn]")
                urn = urn_el.get_attribute("data-urn") if urn_el else ""
                activity_id = ""
                if "activity:" in urn:
                    activity_id = urn.split("activity:")[-1]

                post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/" if activity_id else ""

                # Имя автора
                author_el = container.query_selector(".update-components-actor__name span, .feed-shared-actor__name span")
                author = author_el.inner_text().strip() if author_el else ""

                logger.info(f"\n  📝 Пост от {author[:30]}...")
                logger.info(f"     {post_text[:80]}...")

                # AI: это вакансия?
                data = call_qwen(f"{PARSE_PROMPT}\n\nТекст LinkedIn-поста:\n\n{post_text[:2000]}")
                if not data or not data.get("is_vacancy"):
                    logger.info(f"     ⏭️ Не вакансия")
                    continue

                total_vacancies += 1
                title = data.get("title", "?")
                logger.info(f"     💼 {title} @ {data.get('company', '?')}")

                if check_duplicate(title):
                    logger.info(f"     ⏭️ Дубликат")
                    continue

                # Сопроводительное
                cover, rel = None, None
                if RESUME_TEXT:
                    vacancy_text = f"Должность: {title}\nКомпания: {data.get('company','')}\nЛокация: {data.get('location','')}\nФормат: {data.get('schedule','')}\nЗарплата: {data.get('salary','')}\nТребования: {data.get('notes','')}\n\nПолный текст поста:\n{post_text[:1500]}"
                    result = call_qwen(f"{COVER_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_text}")
                    if result:
                        cover = result.get("cover_letter")
                        rel = result.get("relevance")

                linkedin_url = post_url or f"https://www.linkedin.com/search/results/content/?keywords={encoded}"

                if create_notion_page(data, linkedin_url, author, cover, rel):
                    emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(rel, "")
                    logger.info(f"     ✅ {emoji} Добавлено")
                    total_added += 1
                else:
                    logger.error(f"     ❌ Notion ошибка")

                time.sleep(1)

    finally:
        browser.close()
        pw.stop()

    logger.info(f"\n{'='*50}")
    logger.info(f"📊 Постов: {total_posts} → Вакансий: {total_vacancies} → Добавлено: {total_added}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()

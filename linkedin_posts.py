"""
Job Tracker — Парсинг вакансий из LinkedIn-постов → Notion
Исправления:
- NOTION_HEADERS через функцию (баг с пустым токеном)
- Дедупликация по URL (не по title)
- PM фильтр
- Пагинация (больше постов)
- Фильтр по дате через параметр f_TPR=r5184000 (последние 60 дней ~ с начала 2026)
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
# НАСТРОЙКИ
# ═══════════════════════════════════════

SEARCH_QUERIES = [
    "ищу продакта",
    "ищу product manager",
    "ищем продакт менеджера",
    "вакансия product manager",
    "hiring product manager",
    "product manager вакансия удалёнка",
    "ищем менеджера продукта",
    "открыта вакансия продакт",
]

# Скроллов на каждый запрос (больше = больше постов, дольше)
SCROLL_COUNT = 8

# PM фильтр
PM_KEYWORDS = [
    "product",
    "продукт",
    "продакт",
    "cpo",
    "chief product",
    "руководитель продукт",
]

RELEVANCE_MAP = {"высокая": "🔥 Высокая", "средняя": "👍 Средняя", "низкая": "🤷 Низкая"}

# ═══════════════════════════════════════


def get_notion_headers():
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }


def is_pm_vacancy(title: str) -> bool:
    if not title:
        return False
    return any(kw in title.lower() for kw in PM_KEYWORDS)


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.split("?")[0].split("#")[0]
    return url.rstrip("/").lower()


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

Если автор НЕ ищет сотрудника (ищет работу сам, или это не про найм) — верни: {"is_vacancy": false}

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


def check_duplicate_by_url(linkedin_url: str, vacancy_url: str) -> bool:
    li_norm = normalize_url(linkedin_url)
    vac_norm = normalize_url(vacancy_url)

    if not li_norm and not vac_norm:
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
            db_li = normalize_url(props.get("LinkedIn", {}).get("url") or "")
            db_vac = normalize_url(props.get("Ссылка на вакансию", {}).get("url") or "")

            if li_norm and db_li and li_norm == db_li:
                return True
            if vac_norm and db_vac and vac_norm == db_vac:
                return True

    except Exception as e:
        logger.error(f"Дедупликация: {e}")

    return False


def create_notion_page(vacancy, linkedin_url, cover_letter=None, relevance=None):
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
        sched_map = {
            "офис": "Офис", "office": "Офис",
            "удалёнка": "Удалёнка", "удаленка": "Удалёнка", "remote": "Удалёнка",
            "гибрид": "Гибрид", "hybrid": "Гибрид",
        }
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
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props},
            timeout=30
        )
        return resp.status_code == 200
    except Exception:
        return False


def main():
    total_posts = 0
    total_vacancies = 0
    total_added = 0
    seen_texts = set()

    logger.info("🔗 Парсинг LinkedIn-постов с вакансиями (с начала 2026)\n")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(storage_state=SESSION_FILE)
    page = context.new_page()

    try:
        for query in SEARCH_QUERIES:
            encoded = quote(query)
            # f_TPR=r5184000 = последние 60 дней (с начала 2026)
            url = f"https://www.linkedin.com/search/results/content/?keywords={encoded}&sortBy=%22date_posted%22&f_TPR=r5184000"

            logger.info(f"\n🔍 «{query}»")
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Скроллим для подгрузки большего количества постов
            for i in range(SCROLL_COUNT):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)
                if i % 3 == 2:
                    logger.info(f"   Скролл {i+1}/{SCROLL_COUNT}...")

            post_containers = page.query_selector_all(".occludable-update, .feed-shared-update-v2")
            logger.info(f"   Найдено постов: {len(post_containers)}")

            for container in post_containers:
                text_el = container.query_selector(".update-components-text, .feed-shared-text")
                if not text_el:
                    continue
                post_text = text_el.inner_text().strip()

                if len(post_text) < 50:
                    continue

                # Дедупликация по тексту внутри запуска
                text_key = post_text[:100]
                if text_key in seen_texts:
                    continue
                seen_texts.add(text_key)
                total_posts += 1

                # URN → ссылка на пост
                urn_el = container.query_selector("[data-urn]")
                urn = urn_el.get_attribute("data-urn") if urn_el else ""
                activity_id = urn.split("activity:")[-1] if "activity:" in urn else ""
                post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/" if activity_id else ""

                # Автор
                author_el = container.query_selector(".update-components-actor__name span, .feed-shared-actor__name span")
                author = author_el.inner_text().strip() if author_el else ""

                logger.info(f"\n  📝 {author[:30] or 'Автор неизвестен'}")
                logger.info(f"     {post_text[:80]}...")

                # AI: вакансия?
                data = call_qwen(f"{PARSE_PROMPT}\n\nТекст LinkedIn-поста:\n\n{post_text[:2000]}")
                if not data or not data.get("is_vacancy"):
                    logger.info(f"     ⏭️ Не вакансия")
                    continue

                total_vacancies += 1
                title = data.get("title", "?")

                # PM фильтр
                if not is_pm_vacancy(title):
                    logger.info(f"     ⏭️ Не PM: «{title}»")
                    continue

                logger.info(f"     💼 {title} @ {data.get('company', '?')}")

                # Дедупликация по URL
                if check_duplicate_by_url(post_url, data.get("vacancy_url") or ""):
                    logger.info(f"     ⏭️ Дубликат")
                    continue

                # Сопроводительное
                cover, rel = None, None
                if RESUME_TEXT:
                    vacancy_text = (
                        f"Должность: {title}\nКомпания: {data.get('company','')}\n"
                        f"Локация: {data.get('location','')}\nФормат: {data.get('schedule','')}\n"
                        f"Зарплата: {data.get('salary','')}\nТребования: {data.get('notes','')}\n\n"
                        f"Текст поста:\n{post_text[:1500]}"
                    )
                    result = call_qwen(f"{COVER_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_text}")
                    if result:
                        cover = result.get("cover_letter")
                        rel = result.get("relevance")

                linkedin_url = post_url or f"https://www.linkedin.com/search/results/content/?keywords={encoded}"

                if create_notion_page(data, linkedin_url, cover, rel):
                    emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(rel, "")
                    logger.info(f"     ✅ {emoji} {title} @ {data.get('company', '?')}")
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
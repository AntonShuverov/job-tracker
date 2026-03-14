"""
Job Tracker — Парсинг вакансий с LinkedIn → Notion
Playwright + Qwen AI + Notion API
"""

import os
import re
import json
import logging
import time
import requests

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("linkedin_parser")

from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP,
)

RESUME_TEXT = load_resume()

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")

# ═══════════════════════════════════════
# НАСТРОЙКИ ПОИСКА
# ═══════════════════════════════════════


SEARCH_URLS = [
    ("продакт менеджер", "https://www.linkedin.com/jobs/search/?keywords=%D0%BF%D1%80%D0%BE%D0%B4%D0%B0%D0%BA%D1%82%20%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80&f_TPR=r604800"),
    ("менеджер продукта", "https://www.linkedin.com/jobs/search/?keywords=%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80%20%D0%BF%D1%80%D0%BE%D0%B4%D1%83%D0%BA%D1%82%D0%B0&f_TPR=r604800"),
    ("product manager Москва", "https://www.linkedin.com/jobs/search/?keywords=product%20manager&location=%D0%9C%D0%BE%D1%81%D0%BA%D0%B2%D0%B0&f_TPR=r604800"),
    ("product owner", "https://www.linkedin.com/jobs/search/?keywords=product%20owner&location=Russia&f_TPR=r604800"),
    ("project manager IT Россия", "https://www.linkedin.com/jobs/search/?keywords=project%20manager%20IT&location=Russia&f_TPR=r604800"),
]

MAX_PER_QUERY = 15  # макс вакансий на запрос

# ═══════════════════════════════════════


ANALYZE_PROMPT = """Проанализируй вакансию и резюме.

Верни JSON:
{
  "notes": "Ключевые требования (2-3 предложения)",
  "relevance": "высокая" или "средняя" или "низкая",
  "cover_letter": "Сопроводительное письмо (5-6 предложений)"
}

Правила для письма:
- Если вакансия на русском — "Здравствуйте!". Если на английском — "Hi!"
- 2-3 достижения из резюме С ЦИФРАМИ
- Почему кандидат подходит именно на эту роль
- Тон: профессиональный, живой
- Максимум 5-6 предложений

Релевантность:
- "высокая" — прямое совпадение
- "средняя" — частичное
- "низкая" — мало пересечений

ТОЛЬКО JSON."""


def check_duplicate_by_url(job_url: str) -> bool:
    norm = normalize_url(job_url)
    if not norm:
        return False
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=get_notion_headers(), json=body, timeout=15,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            for page in data.get("results", []):
                db_url = normalize_url(
                    page.get("properties", {}).get("Ссылка на вакансию", {}).get("url") or ""
                )
                if db_url and db_url == norm:
                    return True
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        except Exception as e:
            logger.error(f"check_duplicate_by_url: {e}")
            return False
    return False


def create_notion_page(title, company, location, schedule, linkedin_url, notes, cover_letter, relevance):
    props = {
        "Должность": {"title": [{"text": {"content": title[:100]}}]},
        "Статус": {"select": {"name": "Новая"}},
        "Источник": {"select": {"name": "LinkedIn"}},
        "LinkedIn": {"url": linkedin_url[:200]},
        "Ссылка на вакансию": {"url": linkedin_url[:200]},
    }
    if company:
        props["Компания"] = {"rich_text": [{"text": {"content": company[:100]}}]}
    if location:
        props["Локация"] = {"rich_text": [{"text": {"content": location[:100]}}]}
    if schedule:
        props["Формат работы"] = {"select": {"name": schedule}}
    if notes:
        props["Заметки"] = {"rich_text": [{"text": {"content": notes[:500]}}]}
    if cover_letter:
        props["Сопроводительное письмо"] = {"rich_text": [{"text": {"content": cover_letter[:2000]}}]}
    if relevance:
        props["Релевантность"] = {"select": {"name": RELEVANCE_MAP.get(relevance, "👍 Средняя")}}
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        return resp.status_code == 200
    except Exception:
        return False


def parse_schedule(text):
    text_lower = text.lower()
    if "удаленн" in text_lower or "remote" in text_lower:
        return "Удалёнка"
    elif "гибрид" in text_lower or "hybrid" in text_lower:
        return "Гибрид"
    elif "офис" in text_lower or "on-site" in text_lower or "в офисе" in text_lower:
        return "Офис"
    return "Не указано"


def get_job_description(page, job_url):
    """Открывает вакансию и читает описание."""
    try:
        page.goto(job_url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Кнопка "Показать ещё" для полного описания
        show_more = page.query_selector("[data-tracking-control-name='public_jobs_show-more-html-btn']")
        if not show_more:
            show_more = page.query_selector("button.show-more-less-html__button")
        if show_more:
            show_more.click()
            page.wait_for_timeout(1000)

        desc_el = page.query_selector(".show-more-less-html__markup, .jobs-description__content, .description__text")
        if desc_el:
            return desc_el.inner_text().strip()[:3000]

        # Фоллбэк
        main = page.query_selector("main")
        if main:
            return main.inner_text().strip()[:3000]
    except Exception as e:
        logger.debug(f"Ошибка описания: {e}")
    return ""


def main():
    total_found = 0
    total_added = 0
    seen_urls = set()

    logger.info("🔗 Запуск парсинга LinkedIn вакансий\n")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(storage_state=SESSION_FILE)
    page = context.new_page()

    try:
        for label, search_url in SEARCH_URLS:
            logger.info(f"\n🔍 «{label}»")
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Скроллим для подгрузки
            for _ in range(3):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)

            # Собираем карточки
            cards = page.query_selector_all(".job-card-container, .jobs-search-results__list-item, [data-job-id]")
            logger.info(f"   Карточек: {len(cards)}")

            count = 0
            for card in cards:
                if count >= MAX_PER_QUERY:
                    break

                # Извлекаем данные из карточки
                title_el = card.query_selector("a.job-card-list__title, a.job-card-container__link, [data-control-name='job_card_title']")
                company_el = card.query_selector(".job-card-container__primary-description, .artdeco-entity-lockup__subtitle, .job-card-container__company-name")
                location_el = card.query_selector(".job-card-container__metadata-item, .artdeco-entity-lockup__caption, .job-card-container__metadata-wrapper")

                title = title_el.inner_text().strip() if title_el else ""
                company = company_el.inner_text().strip() if company_el else ""
                location_text = location_el.inner_text().strip() if location_el else ""

                href = title_el.get_attribute("href") if title_el else ""

                if not title or not href:
                    continue

                # Формируем URL
                if href.startswith("/"):
                    job_url = f"https://www.linkedin.com{href}"
                else:
                    job_url = href

                # Дедупликация (нормализованный URL)
                clean_url = normalize_url(job_url)
                if clean_url in seen_urls:
                    continue
                seen_urls.add(clean_url)
                total_found += 1
                count += 1

                # Очищаем title
                title = title.replace(" with verification", "").strip()

                logger.info(f"\n  📋 {title} @ {company}")

                if check_duplicate_by_url(clean_url):
                    logger.info(f"     ⏭️ Дубликат")
                    continue

                # Определяем формат
                schedule = parse_schedule(location_text)
                # Чистим локацию
                location_clean = re.sub(r'\(.*?\)', '', location_text).strip()

                # Открываем вакансию и читаем описание
                logger.info(f"     🔗 Читаю описание...")
                desc_page = context.new_page()
                description = get_job_description(desc_page, job_url)
                desc_page.close()

                # AI анализ
                vacancy_summary = f"Должность: {title}\nКомпания: {company}\nЛокация: {location_clean}\nФормат: {schedule}"
                prompt = f"{ANALYZE_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_summary}\n\n--- ОПИСАНИЕ ---\n{description[:2500]}"

                logger.info(f"     ✍️ Анализ...")
                result = call_qwen(prompt)
                notes = result.get("notes", "") if result else ""
                cover = result.get("cover_letter") if result else None
                rel = result.get("relevance") if result else None

                emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(rel, "")

                if create_notion_page(title, company, location_clean, schedule, clean_url, notes, cover, rel):
                    logger.info(f"     ✅ {emoji} Добавлено")
                    total_added += 1
                else:
                    logger.error(f"     ❌ Notion ошибка")

                time.sleep(1)

    finally:
        browser.close()
        pw.stop()

    logger.info(f"\n{'='*50}")
    logger.info(f"📊 LinkedIn: найдено {total_found} → добавлено {total_added}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()

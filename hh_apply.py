"""
Job Tracker — Автоотклик на hh.ru
Парсит IT-вакансии → анализирует → пишет сопроводительное → откликается → записывает в Notion
"""

import os
import re
import json
import logging
import requests
import time

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("hh_apply")

RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
RESUME_TEXT = ""
if os.path.exists(RESUME_PATH):
    with open(RESUME_PATH, "r", encoding="utf-8") as f:
        RESUME_TEXT = f.read()

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hh_session.json")

# ═══════════════════════════════════════
# НАСТРОЙКИ
# ═══════════════════════════════════════

SEARCH_QUERIES = [
    "product manager",
    "продакт-менеджер",
    "product owner",
    "менеджер продукта",
    "project manager IT",
]
AREAS = ["1", "2", "160"]  # Москва, СПб, Алматы
PER_PAGE = 20
PAGES = 1
IT_ROLES = "96,100,107,112,113,114,116,121,124,125,126"
EXPERIENCE = "between1And3"

# Откликаться только на 🔥 Высокая и 👍 Средняя
APPLY_RELEVANCE = ["высокая", "средняя"]

# Максимум откликов за запуск
MAX_APPLIES = 10

# ═══════════════════════════════════════

HH_HEADERS = {"User-Agent": "JobTracker/1.0 (shuverov.13@gmail.com)"}
NOTION_HEADERS = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
SCHEDULE_MAP = {"fullDay": "Офис", "remote": "Удалёнка", "flexible": "Гибрид", "flyInFlyOut": "Офис", "shift": "Офис"}
RELEVANCE_MAP = {"высокая": "🔥 Высокая", "средняя": "👍 Средняя", "низкая": "🤷 Низкая"}


def search_hh(query, area, page=0):
    params = [
        ("text", query), ("search_field", "name"), ("area", area),
        ("per_page", PER_PAGE), ("page", page), ("order_by", "publication_time"),
    ]
    for rid in IT_ROLES.split(","):
        params.append(("professional_role", rid.strip()))
    if EXPERIENCE:
        params.append(("experience", EXPERIENCE))
    try:
        resp = requests.get("https://api.hh.ru/vacancies", params=params, headers=HH_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("items", [])
    except Exception:
        pass
    return []


def get_vacancy_details(vid):
    try:
        resp = requests.get(f"https://api.hh.ru/vacancies/{vid}", headers=HH_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def format_salary(sal):
    if not sal:
        return None
    parts = []
    if sal.get("from"):
        parts.append(f"от {sal['from']:,}".replace(",", " "))
    if sal.get("to"):
        parts.append(f"до {sal['to']:,}".replace(",", " "))
    cur = {"RUR": "₽", "KZT": "₸", "USD": "$", "EUR": "€"}.get(sal.get("currency", ""), "")
    return " ".join(parts) + f" {cur}" if parts else None


def clean_html(text):
    if not text:
        return ""
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'<[^>]+>', '\n', text)).strip()


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
- Тон: профессиональный, живой, без канцелярита
- Максимум 5-6 предложений

Релевантность:
- "высокая" — прямое совпадение
- "средняя" — частичное
- "низкая" — мало пересечений

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


def create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover_letter, relevance, applied=False):
    from datetime import datetime
    props = {
        "Должность": {"title": [{"text": {"content": title[:100]}}]},
        "Статус": {"select": {"name": "Отправлено" if applied else "Новая"}},
        "Источник": {"select": {"name": "hh.ru"}},
        "Ссылка на вакансию": {"url": hh_url},
    }
    if company:
        props["Компания"] = {"rich_text": [{"text": {"content": company[:100]}}]}
    if salary:
        props["Зарплата"] = {"rich_text": [{"text": {"content": salary[:100]}}]}
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
    if applied:
        from datetime import datetime as dt
        today = dt.now().strftime("%Y-%m-%d")
        props["Дата отправления"] = {"date": {"start": today}}

    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS,
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        return resp.status_code == 200
    except Exception:
        return False


def apply_to_vacancy(page, hh_url, cover_letter):
    """Откликается на вакансию через Playwright."""
    try:
        page.goto(hh_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Ищем кнопку Откликнуться
        apply_btn = page.query_selector("[data-qa='vacancy-response-link-top']")
        if not apply_btn:
            apply_btn = page.query_selector("[data-qa='vacancy-response-link-bottom']")

        if not apply_btn:
            return False, "Кнопка не найдена"

        btn_text = apply_btn.inner_text().strip()
        if "Откликнуться" not in btn_text:
            return False, f"Кнопка: '{btn_text}'"

        # Нажимаем Откликнуться
        apply_btn.click()
        page.wait_for_timeout(2000)

        # Ищем поле для сопроводительного
        letter_input = page.query_selector("textarea[data-qa='vacancy-response-popup-form-letter-input']")
        if not letter_input:
            letter_input = page.query_selector("textarea")

        if letter_input and cover_letter:
            letter_input.fill(cover_letter)
            page.wait_for_timeout(500)

        # Ищем кнопку подтверждения
        submit_btn = page.query_selector("[data-qa='vacancy-response-submit-popup']")
        if not submit_btn:
            submit_btn = page.query_selector("[data-qa='vacancy-response-letter-submit']")
        if not submit_btn:
            # Может быть сразу откликнулось без попапа
            page.wait_for_timeout(1000)
            # Проверяем успех
            success = page.query_selector("[data-qa='vacancy-response-link-top']")
            if success and "Вы откликнулись" in (success.inner_text() or ""):
                return True, "Отклик без попапа"
            return False, "Кнопка отправки не найдена"

        submit_btn.click()
        page.wait_for_timeout(2000)

        return True, "Отклик отправлен"

    except Exception as e:
        return False, str(e)


def main():
    total_found = 0
    total_applied = 0
    total_skipped = 0
    seen_ids = set()

    logger.info("🚀 Запуск автоотклика hh.ru")
    logger.info(f"   Откликаемся на: {', '.join(APPLY_RELEVANCE)}")
    logger.info(f"   Максимум откликов: {MAX_APPLIES}\n")

    # Запускаем Playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(storage_state=SESSION_FILE)
    page = context.new_page()

    try:
        for query in SEARCH_QUERIES:
            if total_applied >= MAX_APPLIES:
                break
            for area in AREAS:
                if total_applied >= MAX_APPLIES:
                    break
                area_names = {"1": "Москва", "2": "СПб", "160": "Алматы"}
                for pg in range(PAGES):
                    if total_applied >= MAX_APPLIES:
                        break
                    logger.info(f"🔍 «{query}» / {area_names.get(area, area)}")
                    items = search_hh(query, area, pg)

                    for item in items:
                        if total_applied >= MAX_APPLIES:
                            break

                        vid = item["id"]
                        if vid in seen_ids:
                            continue
                        seen_ids.add(vid)
                        total_found += 1

                        title = item["name"]
                        company = item.get("employer", {}).get("name", "")
                        salary = format_salary(item.get("salary"))
                        hh_url = item.get("alternate_url", "")
                        location = item.get("area", {}).get("name", "")
                        sched_id = item.get("schedule", {}).get("id", "")
                        schedule = SCHEDULE_MAP.get(sched_id, "Не указано")

                        logger.info(f"\n  📋 {title} @ {company}")

                        if check_duplicate(title):
                            logger.info(f"     ⏭️ Дубликат")
                            continue

                        # Полное описание
                        details = get_vacancy_details(vid)
                        full_desc = clean_html(details.get("description", "")) if details else ""

                        # AI анализ
                        vacancy_summary = f"Должность: {title}\nКомпания: {company}\nЛокация: {location}\nФормат: {schedule}\nЗарплата: {salary or 'не указана'}"
                        prompt = f"{ANALYZE_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_summary}\n\n--- ПОЛНОЕ ОПИСАНИЕ ---\n{full_desc[:2500]}"

                        logger.info(f"     ✍️ Анализ...")
                        result = call_qwen(prompt)
                        notes = result.get("notes", "") if result else ""
                        cover = result.get("cover_letter") if result else None
                        rel = result.get("relevance") if result else None

                        emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(rel, "")
                        logger.info(f"     {emoji} Релевантность: {rel}")

                        # Решаем: откликаться или нет
                        if rel and rel in APPLY_RELEVANCE:
                            logger.info(f"     📨 Откликаюсь...")
                            success, msg = apply_to_vacancy(page, hh_url, cover)
                            if success:
                                logger.info(f"     ✅ {msg}")
                                total_applied += 1
                                create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=True)
                            else:
                                logger.warning(f"     ⚠️ {msg}")
                                create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=False)
                        else:
                            logger.info(f"     ⏭️ Пропускаю (низкая релевантность)")
                            total_skipped += 1
                            create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=False)

                        time.sleep(1)

    finally:
        browser.close()
        pw.stop()

    logger.info(f"\n{'='*50}")
    logger.info(f"📊 Найдено: {total_found}")
    logger.info(f"📨 Откликов: {total_applied}")
    logger.info(f"⏭️ Пропущено: {total_skipped}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()

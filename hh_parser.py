"""
Job Tracker — Парсинг IT-вакансий с hh.ru → Notion
"""

import os
import re
import json
import logging
import requests
import time

from dotenv import load_dotenv
load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("hh_parser")

RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
RESUME_TEXT = ""
if os.path.exists(RESUME_PATH):
    with open(RESUME_PATH, "r", encoding="utf-8") as f:
        RESUME_TEXT = f.read()

# ═══════════════════════════════════════════════
# НАСТРОЙКИ ПОИСКА
# ═══════════════════════════════════════════════

SEARCH_QUERIES = [
    "product manager",
    "продакт-менеджер",
    "product owner",
    "менеджер продукта",
    "project manager IT",
]

# 1=Москва, 2=СПб, 160=Алматы
AREAS = ["1", "2", "160"]

PER_PAGE = 20
PAGES = 2

# Профобласть 1.221 = IT (Информационные технологии)
# https://api.hh.ru/professional_roles
IT_ROLES = "96,100,107,112,113,114,116,121,124,125,126"
# 96=Программист, 100=Аналитик, 107=Руководитель проектов, 112=Тестировщик
# 113=Менеджер продукта, 114=CTO, 116=Системный администратор
# 121=UX, 124=DevOps, 125=Data, 126=AI/ML

EXPERIENCE = "between1And3"

# ═══════════════════════════════════════════════

HH_HEADERS = {"User-Agent": "JobTracker/1.0 (shuverov.13@gmail.com)"}
NOTION_HEADERS = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
SCHEDULE_MAP_HH = {"fullDay": "Офис", "remote": "Удалёнка", "flexible": "Гибрид", "flyInFlyOut": "Офис", "shift": "Офис"}
RELEVANCE_MAP = {"высокая": "🔥 Высокая", "средняя": "👍 Средняя", "низкая": "🤷 Низкая"}


def search_hh(query, area, page=0):
    params = [
        ("text", query),
        ("search_field", "name"),
        ("area", area),
        ("per_page", PER_PAGE),
        ("page", page),
        ("order_by", "publication_time"),
    ]
    for role_id in IT_ROLES.split(","):
        params.append(("professional_role", role_id.strip()))
    if EXPERIENCE:
        params.append(("experience", EXPERIENCE))
    try:
        resp = requests.get("https://api.hh.ru/vacancies", params=params, headers=HH_HEADERS, timeout=15)
        if resp.status_code == 200:
            return resp.json().get("items", [])
        logger.error(f"hh.ru {resp.status_code}")
    except Exception as e:
        logger.error(f"hh.ru: {e}")
    return []


def get_vacancy_details(vacancy_id):
    try:
        resp = requests.get(f"https://api.hh.ru/vacancies/{vacancy_id}", headers=HH_HEADERS, timeout=15)
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
    cur = {"RUR": "₽", "KZT": "₸", "USD": "$", "EUR": "€"}.get(sal.get("currency", ""), sal.get("currency", ""))
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


ANALYZE_PROMPT = """Проанализируй вакансию и резюме кандидата.

Верни JSON:
{
  "notes": "Ключевые требования и обязанности (2-3 предложения)",
  "relevance": "высокая" или "средняя" или "низкая",
  "cover_letter": "Сопроводительное письмо (5-6 предложений)"
}

Правила для письма:
- Язык = язык вакансии
- Если вакансия на русском — "Здравствуйте!". Если на английском — "Hi!"
- 2-3 достижения из резюме С ЦИФРАМИ
- Почему кандидат подходит
- Тон: профессиональный, живой
- Максимум 5-6 предложений

Релевантность:
- "высокая" — прямое совпадение опыта и навыков
- "средняя" — частичное
- "низкая" — мало пересечений

ТОЛЬКО JSON."""


def analyze_vacancy(vacancy_summary, full_description):
    prompt = f"{ANALYZE_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_summary}\n\n--- ПОЛНОЕ ОПИСАНИЕ ---\n{full_description[:2500]}"
    return call_qwen(prompt)


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


def create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover_letter, relevance):
    props = {
        "Должность": {"title": [{"text": {"content": title[:100]}}]},
        "Статус": {"select": {"name": "Новая"}},
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
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS,
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        return resp.status_code == 200
    except Exception:
        return False


def main():
    total_found = 0
    total_added = 0
    seen_ids = set()

    for query in SEARCH_QUERIES:
        for area in AREAS:
            area_names = {"1": "Москва", "2": "СПб", "160": "Алматы"}
            for page in range(PAGES):
                logger.info(f"\n🔍 «{query}» / {area_names.get(area, area)} / стр.{page+1}")
                items = search_hh(query, area, page)

                for item in items:
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
                    schedule = SCHEDULE_MAP_HH.get(sched_id, "Не указано")

                    logger.info(f"  📋 {title} @ {company}")

                    if check_duplicate(title):
                        logger.info(f"     ⏭️ Дубликат")
                        continue

                    details = get_vacancy_details(vid)
                    full_desc = clean_html(details.get("description", "")) if details else ""

                    vacancy_summary = f"Должность: {title}\nКомпания: {company}\nЛокация: {location}\nФормат: {schedule}\nЗарплата: {salary or 'не указана'}"

                    logger.info(f"     ✍️ Анализ...")
                    result = analyze_vacancy(vacancy_summary, full_desc)

                    notes = result.get("notes", "") if result else ""
                    cover = result.get("cover_letter") if result else None
                    rel = result.get("relevance") if result else None

                    if create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel):
                        emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(rel, "")
                        logger.info(f"     ✅ {emoji} Добавлено")
                        total_added += 1
                    else:
                        logger.error(f"     ❌ Notion ошибка")

                    time.sleep(0.5)

    logger.info(f"\n{'='*50}")
    logger.info(f"📊 hh.ru: найдено {total_found} → добавлено {total_added}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()

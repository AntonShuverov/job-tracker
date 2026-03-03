"""
Job Tracker — hh.ru парсер
Исправления: get_notion_headers(), деdup по URL, date_from фильтр, лог в файл
"""

import os, re, json, logging, requests, time
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hh_parser.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger("hh_parser")

RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
RESUME_TEXT = open(RESUME_PATH, encoding="utf-8").read() if os.path.exists(RESUME_PATH) else ""

SEARCH_QUERIES = [
    "product manager", "продакт-менеджер", "product owner",
    "менеджер продукта", "head of product", "CPO", "менеджер продукта AI",
]
AREAS = ["1", "2", "160"]
PER_PAGE = 20
PAGES = 3
IT_ROLES = "96,100,107,112,113,114,116,121,124,125,126"
EXPERIENCE = "between1And3"
DATE_FROM_DAYS = 60  # последние 60 дней = с начала 2026

HH_HEADERS = {"User-Agent": "JobTracker/1.0 (shuverov.13@gmail.com)"}
SCHEDULE_MAP = {"fullDay": "Офис", "remote": "Удалёнка", "flexible": "Гибрид", "flyInFlyOut": "Офис", "shift": "Офис"}
RELEVANCE_MAP = {"высокая": "🔥 Высокая", "средняя": "👍 Средняя", "низкая": "🤷 Низкая"}


def get_notion_headers():
    return {"Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}


def normalize_url(url):
    if not url: return ""
    return url.split("?")[0].split("#")[0].rstrip("/").lower()


def check_duplicate_by_url(hh_url):
    norm = normalize_url(hh_url)
    if not norm: return False
    try:
        resp = requests.post(f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=get_notion_headers(), json={"page_size": 100}, timeout=15)
        if resp.status_code != 200: return False
        for page in resp.json().get("results", []):
            db_url = normalize_url(page.get("properties", {}).get("Ссылка на вакансию", {}).get("url") or "")
            if db_url and db_url == norm: return True
    except Exception as e:
        logger.error(f"Дедупликация: {e}")
    return False


def search_hh(query, area, page=0):
    date_from = (datetime.now() - timedelta(days=DATE_FROM_DAYS)).strftime("%Y-%m-%d")
    params = [("text", query), ("search_field", "name"), ("area", area),
              ("per_page", PER_PAGE), ("page", page), ("order_by", "publication_time"), ("date_from", date_from)]
    for rid in IT_ROLES.split(","): params.append(("professional_role", rid.strip()))
    if EXPERIENCE: params.append(("experience", EXPERIENCE))
    try:
        resp = requests.get("https://api.hh.ru/vacancies", params=params, headers=HH_HEADERS, timeout=15)
        if resp.status_code == 200: return resp.json().get("items", [])
        logger.error(f"hh.ru {resp.status_code}")
    except Exception as e:
        logger.error(f"hh.ru: {e}")
    return []


def get_vacancy_details(vid):
    try:
        resp = requests.get(f"https://api.hh.ru/vacancies/{vid}", headers=HH_HEADERS, timeout=15)
        if resp.status_code == 200: return resp.json()
    except: pass
    return None


def format_salary(sal):
    if not sal: return None
    parts = []
    if sal.get("from"): parts.append(f"от {sal['from']:,}".replace(",", " "))
    if sal.get("to"): parts.append(f"до {sal['to']:,}".replace(",", " "))
    cur = {"RUR": "₽", "KZT": "₸", "USD": "$", "EUR": "€"}.get(sal.get("currency", ""), "")
    return " ".join(parts) + f" {cur}" if parts else None


def clean_html(text):
    if not text: return ""
    return re.sub(r'\n{3,}', '\n\n', re.sub(r'<[^>]+>', '\n', text)).strip()


def call_qwen(prompt, max_tokens=800):
    try:
        resp = requests.post(QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=30)
        if resp.status_code != 200: return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m: return json.loads(m.group())
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
- Почему кандидат подходит именно на эту роль
- Тон: профессиональный, живой, без канцелярита
- Максимум 5-6 предложений

Релевантность: "высокая" / "средняя" / "низкая"
ТОЛЬКО JSON."""


def analyze_vacancy(summary, desc):
    return call_qwen(f"{ANALYZE_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{summary}\n\n--- ОПИСАНИЕ ---\n{desc[:2500]}")


def create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel):
    props = {
        "Должность": {"title": [{"text": {"content": title[:100]}}]},
        "Статус": {"select": {"name": "Новая"}},
        "Источник": {"select": {"name": "hh.ru"}},
        "Ссылка на вакансию": {"url": hh_url},
    }
    if company: props["Компания"] = {"rich_text": [{"text": {"content": company[:100]}}]}
    if salary: props["Зарплата"] = {"rich_text": [{"text": {"content": salary[:100]}}]}
    if location: props["Локация"] = {"rich_text": [{"text": {"content": location[:100]}}]}
    if schedule: props["Формат работы"] = {"select": {"name": schedule}}
    if notes: props["Заметки"] = {"rich_text": [{"text": {"content": notes[:500]}}]}
    if cover: props["Сопроводительное письмо"] = {"rich_text": [{"text": {"content": cover[:2000]}}]}
    if rel: props["Релевантность"] = {"select": {"name": RELEVANCE_MAP.get(rel, "👍 Средняя")}}
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        return resp.status_code == 200
    except: return False


def main():
    total_found = total_added = 0
    seen_ids = set()
    area_names = {"1": "Москва", "2": "СПб", "160": "Алматы"}

    logger.info(f"🚀 hh.ru парсер | последние {DATE_FROM_DAYS} дней")
    logger.info(f"   {len(SEARCH_QUERIES)} запросов × {len(AREAS)} регионов × {PAGES} стр.\n")

    for query in SEARCH_QUERIES:
        for area in AREAS:
            for pg in range(PAGES):
                logger.info(f"\n🔍 «{query}» / {area_names.get(area, area)} / стр.{pg+1}")
                items = search_hh(query, area, pg)
                if not items:
                    logger.info("   Нет результатов")
                    continue

                for item in items:
                    vid = item["id"]
                    if vid in seen_ids: continue
                    seen_ids.add(vid)
                    total_found += 1

                    title = item["name"]
                    company = item.get("employer", {}).get("name", "")
                    salary = format_salary(item.get("salary"))
                    hh_url = item.get("alternate_url", "")
                    location = item.get("area", {}).get("name", "")
                    schedule = SCHEDULE_MAP.get(item.get("schedule", {}).get("id", ""), "Не указано")

                    logger.info(f"  📋 {title} @ {company}")

                    if check_duplicate_by_url(hh_url):
                        logger.info(f"     ⏭️  Дубликат"); continue

                    details = get_vacancy_details(vid)
                    full_desc = clean_html(details.get("description", "")) if details else ""
                    summary = f"Должность: {title}\nКомпания: {company}\nЛокация: {location}\nФормат: {schedule}\nЗарплата: {salary or 'не указана'}"

                    logger.info(f"     ✍️  Анализ AI...")
                    result = analyze_vacancy(summary, full_desc)
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
"""
Job Tracker — Автоотклик на hh.ru
Исправления: get_notion_headers(), деdup по URL, PM фильтр, обработка вопросов работодателя, лог в файл
"""

import os, re, json, logging, requests, time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()

from common import (
    get_notion_headers, normalize_url, load_resume, call_qwen,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, RELEVANCE_MAP,
)

NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hh_apply.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger("hh_apply")

RESUME_TEXT = load_resume()
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hh_session.json")

SEARCH_QUERIES = [
    "product manager", "продакт-менеджер", "product owner",
    "менеджер продукта", "head of product", "продуктовый аналитик",
    "product analyst", "аналитик продукта",
]
AREAS = ["1", "2", "160"]
PER_PAGE = 20
PAGES = 10
IT_ROLES = "96,100,107,112,113,114,116,121,124,125,126"
EXPERIENCE = "between1And3"
DATE_FROM_DAYS = 60
APPLY_RELEVANCE = ["высокая", "средняя"]
MAX_APPLIES = 200

# PM фильтр — только продуктовые роли
PM_KEYWORDS = [
    "product", "продукт", "продакт", "cpo", "chief product",
    "head of product", "product analyst", "продуктовый аналитик",
    "аналитик продукта", "product owner",
]

# Параметры для ответов на вопросы работодателя
DESIRED_SALARY = "200000"
DESIRED_CITY = "Москва"
READY_TO_RELOCATE = "Да"

HH_HEADERS = {"User-Agent": "JobTracker/1.0 (shuverov.13@gmail.com)"}
SCHEDULE_MAP = {"fullDay": "Офис", "remote": "Удалёнка", "flexible": "Гибрид", "flyInFlyOut": "Офис", "shift": "Офис"}

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
    except: pass
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



ANALYZE_PROMPT = """Проанализируй вакансию и резюме.

Верни JSON:
{
  "notes": "Ключевые требования (2-3 предложения)",
  "relevance": "высокая" или "средняя" или "низкая",
  "cover_letter": "Сопроводительное письмо (5-6 предложений)"
}

Правила:
- Язык = язык вакансии
- Если на русском — "Здравствуйте!". Если на английском — "Hi!"
- 2-3 достижения из резюме С ЦИФРАМИ
- Почему кандидат подходит
- Тон: профессиональный, живой
- Максимум 5-6 предложений

ТОЛЬКО JSON."""


def analyze_vacancy(summary, desc):
    return call_qwen(f"{ANALYZE_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{summary}\n\n--- ОПИСАНИЕ ---\n{desc[:2500]}")


def fill_employer_questions(page):
    try:
        salary_inputs = page.query_selector_all("input[data-qa='vacancy-response-popup-salary']")
        for inp in salary_inputs:
            inp.fill(DESIRED_SALARY)

        question_blocks = page.query_selector_all("[data-qa='task-response-question']")
        for block in question_blocks:
            question_text = block.inner_text().strip()
            answer_input = block.query_selector("textarea, input[type='text']")
            if not answer_input:
                continue
            q_lower = question_text.lower()
            if any(w in q_lower for w in ["город", "city", "локац"]):
                answer_input.fill(DESIRED_CITY)
            elif any(w in q_lower for w in ["релокац", "переезд", "reloc"]):
                answer_input.fill(READY_TO_RELOCATE)
            elif any(w in q_lower for w in ["зарплат", "salary", "ожидан"]):
                answer_input.fill(DESIRED_SALARY)
            else:
                result = call_qwen(
                    f"Ответь коротко (1-2 предложения) на вопрос работодателя от имени кандидата.\n"
                    f"Вопрос: {question_text}\n"
                    f"Резюме кандидата: {RESUME_TEXT[:500]}\n"
                    f"Верни ТОЛЬКО текст ответа, без JSON.",
                    max_tokens=100
                )
                if result and isinstance(result, str):
                    answer_input.fill(result[:200])

        selects = page.query_selector_all("select[data-qa]")
        for sel in selects:
            options = sel.query_selector_all("option")
            if options and len(options) > 1:
                for opt in options[1:]:
                    val = opt.get_attribute("value")
                    if val:
                        sel.select_option(val)
                        break
    except Exception as e:
        logger.debug(f"Вопросы работодателя: {e}")


def apply_to_vacancy(page, hh_url, cover_letter):
    try:
        page.goto(hh_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # Закрываем cookie-баннер если появился
        try:
            cookie_btn = page.query_selector("button[data-qa='cookie-agreement-button']") or \
                         page.query_selector("button:has-text('Понятно')")
            if cookie_btn:
                cookie_btn.click()
                page.wait_for_timeout(500)
        except Exception:
            pass

        # Ждём появления кнопки (React рендерит её асинхронно)
        apply_btn = None
        for selector in ["[data-qa='vacancy-response-link-top']", "[data-qa='vacancy-response-link-bottom']"]:
            try:
                page.wait_for_selector(selector, timeout=8000)
                apply_btn = page.query_selector(selector)
                if apply_btn:
                    break
            except Exception:
                pass

        if not apply_btn:
            current_url = page.url
            page_text = page.inner_text("body")[:300] if page.query_selector("body") else ""
            logger.warning(f"     URL после загрузки: {current_url}")
            logger.warning(f"     Текст страницы: {page_text[:200]!r}")
            return False, "Кнопка откликнуться не найдена"

        btn_text = apply_btn.inner_text().strip()
        if "Откликнуться" not in btn_text:
            return False, f"Уже откликнулся или кнопка: «{btn_text}»"

        apply_btn.scroll_into_view_if_needed()
        apply_btn.click()
        page.wait_for_timeout(2500)

        letter_input = (
            page.query_selector("textarea[data-qa='vacancy-response-popup-form-letter-input']") or
            page.query_selector("textarea")
        )
        if letter_input and cover_letter:
            letter_input.fill(cover_letter)
            page.wait_for_timeout(500)

        fill_employer_questions(page)
        page.wait_for_timeout(500)

        submit_btn = (
            page.query_selector("[data-qa='vacancy-response-submit-popup']") or
            page.query_selector("[data-qa='vacancy-response-letter-submit']")
        )
        if not submit_btn:
            return False, "Кнопка отправки не найдена"

        submit_btn.click()
        page.wait_for_timeout(2000)
        return True, "Отклик отправлен"

    except Exception as e:
        return False, str(e)


def create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=False):
    today = datetime.now().strftime("%Y-%m-%d")
    props = {
        "Должность": {"title": [{"text": {"content": title[:100]}}]},
        "Статус": {"select": {"name": "Отправлено" if applied else "Новая"}},
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
    if applied: props["Дата отправления"] = {"date": {"start": today}}
    try:
        resp = requests.post("https://api.notion.com/v1/pages", headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props}, timeout=30)
        return resp.status_code == 200
    except: return False


def main():
    total_found = total_applied = total_skipped = 0
    seen_ids = set()
    area_names = {"1": "Москва", "2": "СПб", "160": "Алматы"}

    logger.info("🚀 Автоотклик hh.ru запущен")
    logger.info(f"   Откликаемся на: {', '.join(APPLY_RELEVANCE)} | Максимум: {MAX_APPLIES}\n")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(storage_state=SESSION_FILE)
    page = context.new_page()

    try:
        for query in SEARCH_QUERIES:
            if total_applied >= MAX_APPLIES: break
            for area in AREAS:
                if total_applied >= MAX_APPLIES: break
                for pg in range(PAGES):
                    if total_applied >= MAX_APPLIES: break

                    logger.info(f"\n🔍 «{query}» / {area_names.get(area, area)}")
                    items = search_hh(query, area, pg)

                    for item in items:
                        if total_applied >= MAX_APPLIES: break

                        vid = item["id"]
                        if vid in seen_ids: continue
                        seen_ids.add(vid)
                        total_found += 1

                        title = item["name"]

                        # PM фильтр
                        if not any(kw in title.lower() for kw in PM_KEYWORDS):
                            logger.info(f"  ⏭️  Не PM: «{title}» — пропускаю")
                            continue

                        company = item.get("employer", {}).get("name", "")
                        salary = format_salary(item.get("salary"))
                        hh_url = item.get("alternate_url", "")
                        location = item.get("area", {}).get("name", "")
                        schedule = SCHEDULE_MAP.get(item.get("schedule", {}).get("id", ""), "Не указано")

                        logger.info(f"\n  📋 {title} @ {company}")

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

                        emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(rel, "")
                        logger.info(f"     {emoji} Релевантность: {rel}")

                        if rel and rel in APPLY_RELEVANCE:
                            logger.info(f"     📨 Откликаюсь...")
                            success, msg = apply_to_vacancy(page, hh_url, cover)
                            if success:
                                logger.info(f"     ✅ {msg}")
                                total_applied += 1
                                create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=True)
                            else:
                                logger.warning(f"     ⚠️  {msg}")
                                create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=False)
                        else:
                            logger.info(f"     ⏭️  Пропускаю (низкая релевантность)")
                            total_skipped += 1
                            create_notion_page(title, company, salary, location, schedule, hh_url, notes, cover, rel, applied=False)

                        time.sleep(1)

    finally:
        browser.close()
        pw.stop()

    logger.info(f"\n{'='*50}")
    logger.info(f"📊 Найдено: {total_found} | Откликов: {total_applied} | Пропущено: {total_skipped}")
    logger.info(f"{'='*50}")


if __name__ == "__main__":
    main()
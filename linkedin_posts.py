"""
linkedin_posts.py v3 — парсинг через inner_text страницы
LinkedIn изменил DOM, поэтому парсим текст напрямую без CSS-селекторов
"""

import os, re, json, logging, time, requests
from urllib.parse import quote
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("linkedin")

from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP,
)

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")
RESUME_TEXT  = load_resume()

SEARCH_QUERIES = [
    "ищу продакта",
    "ищу product manager",
    "hiring product manager",
    "вакансия продакт менеджер",
    "ищем менеджера продукта",
    "product manager вакансия",
    "открыта вакансия продакт",
    "ищем продакт менеджера",
]

SCROLL_COUNT  = 8
PM_KEYWORDS   = ["product", "продукт", "продакт", "cpo", "chief product", "head of product"]
# Мусорные строки которые не являются частью поста
JUNK_LINES = {
    "публикация в ленте", "нравится", "комментировать", "поделиться", "отправить",
    "реакций", "репоста", "репост", "развернуть", "отслеживать", "подписаться",
    "2-й", "1-й", "3-й", "• ", "···", "...",
    "главная", "сеть", "вакансии", "сообщения", "уведомления", "профиль",
    "для бизнеса", "попробовать premium", "перейти к основному контенту",
    "самые последние", "дата размещения", "тип контента", "от участника",
    "все фильтры", "сброс", "0 уведомлений",
}


PARSE_PROMPT = """\
Проанализируй текст LinkedIn-поста. Автор ИЩЕТ сотрудника?

Если НЕ ищет — верни: {"is_vacancy": false}

Если ищет сотрудника:
{
  "is_vacancy": true,
  "title": "Название должности",
  "company": "Компания или null",
  "schedule": "Офис / Удалёнка / Гибрид / Не указано",
  "location": "Город или null",
  "salary": "Зарплата или null",
  "email": "Email или null",
  "tg_contact": "@username или null",
  "vacancy_url": "Ссылка из текста или null",
  "notes": "Требования кратко (2-3 предложения)"
}
ТОЛЬКО JSON."""

COVER_PROMPT = """\
Вакансия и резюме. Верни JSON:
{
  "relevance": "высокая" | "средняя" | "низкая",
  "cover_letter": "Письмо 5-6 предложений"
}
Правила: язык вакансии, 2-3 цифры из резюме, профессиональный тон. ТОЛЬКО JSON."""


def is_duplicate(linkedin_url, vacancy_url):
    li  = normalize_url(linkedin_url)
    vac = normalize_url(vacancy_url or "")
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            r = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=get_notion_headers(), json=body, timeout=15)
            if r.status_code != 200:
                return False
            data = r.json()
            for page in data.get("results", []):
                p = page.get("properties", {})
                db_li  = normalize_url(p.get("LinkedIn", {}).get("url") or "")
                db_vac = normalize_url(p.get("Ссылка на вакансию", {}).get("url") or "")
                if (li and db_li and li == db_li) or (vac and db_vac and vac == db_vac):
                    return True
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        except Exception as e:
            log.error(f"is_duplicate: {e}")
            return False
    return False


def save_to_notion(vacancy, linkedin_url, cover=None, rel=None):
    props = {
        "Должность":          {"title": [{"text": {"content": vacancy.get("title", "?")[:100]}}]},
        "Статус":             {"select": {"name": "Новая"}},
        "Источник":           {"select": {"name": "LinkedIn"}},
        "LinkedIn":           {"url": linkedin_url[:200]},
        "Ссылка на вакансию": {"url": (vacancy.get("vacancy_url") or linkedin_url)[:200]},
    }
    if vacancy.get("company"):    props["Компания"]   = {"rich_text": [{"text": {"content": vacancy["company"][:100]}}]}
    if vacancy.get("tg_contact"): props["Контакт ТГ"] = {"rich_text": [{"text": {"content": vacancy["tg_contact"][:100]}}]}
    if vacancy.get("email"):      props["Email"]       = {"rich_text": [{"text": {"content": vacancy["email"][:100]}}]}
    if vacancy.get("location"):   props["Локация"]     = {"rich_text": [{"text": {"content": vacancy["location"][:100]}}]}
    if vacancy.get("salary"):     props["Зарплата"]    = {"rich_text": [{"text": {"content": vacancy["salary"][:100]}}]}
    if vacancy.get("notes"):      props["Заметки"]     = {"rich_text": [{"text": {"content": vacancy["notes"][:500]}}]}
    if cover: props["Сопроводительное письмо"] = {"rich_text": [{"text": {"content": cover[:2000]}}]}
    if rel:   props["Релевантность"] = {"select": {"name": RELEVANCE_MAP.get(rel, "👍 Средняя")}}
    if vacancy.get("schedule"):
        smap = {"офис":"Офис","office":"Офис","удалёнка":"Удалёнка","удаленка":"Удалёнка",
                "remote":"Удалёнка","гибрид":"Гибрид","hybrid":"Гибрид"}
        s = smap.get(vacancy["schedule"].lower(), vacancy["schedule"])
        if s in {"Офис","Удалёнка","Гибрид","Не указано"}:
            props["Формат работы"] = {"select": {"name": s}}
    try:
        r = requests.post("https://api.notion.com/v1/pages",
            headers=get_notion_headers(),
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props},
            timeout=30)
        if r.status_code == 200:
            return True
        log.error(f"Notion {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.error(f"save_to_notion: {e}")
    return False


def split_page_into_posts(page_text: str) -> list[str]:
    """
    Разбивает текст страницы на отдельные посты.
    Разделитель — строка 'Публикация в ленте'.
    """
    # Разбиваем по разделителю
    raw_blocks = re.split(r'(?i)публикация в ленте', page_text)

    posts = []
    for block in raw_blocks:
        # Чистим строки — убираем мусор
        lines = []
        for line in block.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            # Пропускаем мусорные строки
            low = stripped.lower()
            if any(low == j or low.startswith(j) for j in JUNK_LINES):
                continue
            # Пропускаем строки типа "25 реакций", "3 репоста", "2 нед."
            if re.match(r'^\d+\s*(реакц|репост|нед\.|дн\.|мес\.|ч\.|мин\.)', low):
                continue
            # Пропускаем пустые и слишком короткие
            if len(stripped) < 3:
                continue
            lines.append(stripped)

        text = "\n".join(lines).strip()
        if len(text) > 80:  # минимальная длина осмысленного поста
            posts.append(text)

    return posts


def main():
    total_posts = total_vacancies = total_added = 0
    seen = set()

    log.info("🔗 LinkedIn парсинг v3\n")

    pw      = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    ctx     = browser.new_context(storage_state=SESSION_FILE)
    page    = ctx.new_page()

    # Проверяем авторизацию
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if "authwall" in page.url or "login" in page.url:
        log.error("❌ Сессия LinkedIn протухла! Запусти python3 linkedin_login.py")
        browser.close(); pw.stop()
        return
    log.info("✅ Авторизация LinkedIn OK\n")

    try:
        for query in SEARCH_QUERIES:
            encoded = quote(query)
            url = (
                f"https://www.linkedin.com/search/results/content/"
                f"?keywords={encoded}&sortBy=%22date_posted%22&f_TPR=r604800"
            )

            log.info(f"\n🔍 «{query}»")
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)

            # Скролл для подгрузки постов
            for i in range(SCROLL_COUNT):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1800)

            # Собираем URL постов из DOM (ссылки вида /feed/update/urn:li:activity:...)
            post_urls_pool = []
            seen_hrefs = set()
            for link in page.query_selector_all("a[href*='/feed/update/']"):
                href = link.get_attribute("href") or ""
                if "activity" not in href:
                    continue
                if href.startswith("/"):
                    href = "https://www.linkedin.com" + href
                href = href.split("?")[0]
                if href not in seen_hrefs:
                    seen_hrefs.add(href)
                    post_urls_pool.append(href)
            log.info(f"   Найдено URL постов в DOM: {len(post_urls_pool)}")

            # Берём весь текст страницы и режем на посты
            body_text = page.inner_text("body")
            posts = split_page_into_posts(body_text)
            log.info(f"   Найдено постов: {len(posts)}")

            url_idx = 0
            for post_text in posts:
                key = post_text[:120]
                if key in seen:
                    continue
                seen.add(key)
                total_posts += 1

                log.info(f"\n  📝 {post_text[:90]}...")

                # AI: вакансия?
                data = call_qwen(f"{PARSE_PROMPT}\n\nТекст поста:\n\n{post_text[:2000]}")
                if not data or not data.get("is_vacancy"):
                    log.info("     ⏭  Не вакансия")
                    url_idx += 1
                    continue

                title = data.get("title", "?")
                if not any(kw in title.lower() for kw in PM_KEYWORDS):
                    log.info(f"     ⏭  Не PM: «{title}»")
                    url_idx += 1
                    continue

                total_vacancies += 1
                log.info(f"     💼 {title} @ {data.get('company', '?')}")

                # Берём LinkedIn URL из пула DOM-ссылок по индексу
                post_url = (post_urls_pool[url_idx] if url_idx < len(post_urls_pool)
                            else f"https://www.linkedin.com/search/results/content/?keywords={encoded}")
                url_idx += 1

                if is_duplicate(post_url, data.get("vacancy_url")):
                    log.info("     ⏭  Дубликат")
                    continue

                # Сопроводительное
                cover, rel = None, None
                if RESUME_TEXT:
                    vt = (f"Должность: {title}\nКомпания: {data.get('company','')}\n"
                          f"Локация: {data.get('location','')}\nФормат: {data.get('schedule','')}\n"
                          f"Зарплата: {data.get('salary','')}\nТребования: {data.get('notes','')}\n\n"
                          f"Текст поста:\n{post_text[:1500]}")
                    res = call_qwen(f"{COVER_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vt}")
                    if res:
                        cover = res.get("cover_letter")
                        rel   = res.get("relevance")

                if save_to_notion(data, post_url, cover, rel):
                    emoji = {"высокая":"🔥","средняя":"👍","низкая":"🤷"}.get(rel, "")
                    log.info(f"     ✅ {emoji} Сохранено")
                    total_added += 1

                time.sleep(1)

    finally:
        browser.close()
        pw.stop()

    log.info(f"\n{'='*50}")
    log.info(f"📊 Постов: {total_posts} → Вакансий: {total_vacancies} → Добавлено: {total_added}")
    log.info(f"{'='*50}")


if __name__ == "__main__":
    main()

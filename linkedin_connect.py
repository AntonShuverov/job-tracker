"""
linkedin_connect.py — Автоматические приглашения на LinkedIn
Ищет сотрудников IT-компаний через people search.
Нажимает «Установить контакт» прямо из карточки — без захода в профиль.
"""

import os, re, logging, time, random
from urllib.parse import quote
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
load_dotenv()

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_connect.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
log = logging.getLogger("linkedin_connect")

SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")

# ─── Лимиты ────────────────────────────────────────────────────────────────
MAX_INVITES_PER_RUN = 10
DELAY_BETWEEN = (8, 16)

# ─── Компании + роли (используются как keywords для поиска) ─────────────────
SEARCHES = [
    # Казахстан
    ("Freedom Holding Corp", "product manager"),
    ("Freedom Holding Corp", "recruiter"),
    ("Kaspi Bank", "product manager"),
    ("Kaspi Bank", "recruiter"),
    ("Kolesa Group", "product manager"),
    ("Chocofamily", "product manager"),
    ("Chocofamily", "recruiter"),
    # Россия
    ("Яндекс", "product manager"),
    ("Яндекс", "recruiter"),
    ("Tinkoff", "product manager"),
    ("Tinkoff", "recruiter"),
    ("Avito", "product manager"),
    ("Avito", "recruiter"),
    ("Wildberries", "product manager"),
    ("2GIS", "product manager"),
    ("JetBrains", "product manager"),
    ("Ozon", "product manager"),
    ("Sber", "product manager"),
]

PRODUCT_KW  = ["product", "продукт", "продакт", "cpo", "chief product", "head of product", "owner"]
RECRUITER_KW = ["recruit", "рекрутёр", "рекрутер", "talent", "hr", "hiring", "подбор"]


def classify_title(title: str) -> str:
    t = title.lower()
    if any(k in t for k in RECRUITER_KW):
        return "рекрутёр"
    if any(k in t for k in PRODUCT_KW):
        return "продакт"
    return "другое"


def build_url(company: str, role: str) -> str:
    kw = f"{company} {role}"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(kw)}&origin=FACETED_SEARCH"


def send_invite_modal(page) -> bool:
    """Обрабатывает диалог после нажатия Connect — отправляет без записки."""
    page.wait_for_timeout(1500)
    send_btn = (
        page.query_selector("button[aria-label*='Отправить без']") or
        page.query_selector("button:has-text('Отправить без записки')") or
        page.query_selector("button:has-text('Send without a note')") or
        page.query_selector("button[aria-label='Send now']") or
        page.query_selector("button:has-text('Send now')") or
        page.query_selector("button:has-text('Отправить сейчас')") or
        # Если сразу нет диалога — просто "Отправить"
        page.query_selector("button[aria-label='Отправить']") or
        page.query_selector("button[aria-label*='Send invitation']")
    )
    if send_btn and send_btn.is_visible():
        send_btn.click()
        page.wait_for_timeout(1000)
        return True
    # Если диалога нет — приглашение уже отправлено без диалога (редко)
    page.keyboard.press("Escape")
    return False


def process_search_page(page, role_kw: str, seen: set, max_left: int) -> tuple[list[str], int, int]:
    """
    Парсит страницу поиска, кликает Connect прямо из карточек.
    Возвращает (sent_urls, sent_count, skipped_count).
    """
    sent = []
    sent_count = skipped_count = 0

    # Подгрузить карточки
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)

    # Настоящая кнопка: <a href="/preload/search-custom-invite/?vanityName=...">
    connect_btns = (
        page.query_selector_all("a[href*='search-custom-invite']") or
        page.query_selector_all("button[aria-label*='установить контакт']") or
        page.query_selector_all("a:has-text('Connect')")
    )
    log.info(f"   Кнопок Connect: {len(connect_btns)}")

    for btn in connect_btns:
        if sent_count >= max_left:
            break

        # Имя и профиль из href кнопки: /preload/search-custom-invite/?vanityName=xxx
        href_val = btn.get_attribute("href") or ""
        vanity_m = re.search(r"vanityName=([^&]+)", href_val)
        vanity = vanity_m.group(1) if vanity_m else ""
        profile_url = f"https://www.linkedin.com/in/{vanity}" if vanity else ""
        name = vanity or "?"
        # Имя — из родительской карточки
        try:
            name = btn.evaluate(
                "el => { let p = el; for(let i=0;i<10;i++){ p=p.parentElement; "
                "if(!p) break; "
                "let a = p.querySelector('a[href*=\"/in/\"]'); "
                "if(a) return a.innerText.split('\\n')[0].trim(); } return ''; }"
            ) or vanity or "?"
        except Exception:
            pass

        if profile_url in seen:
            continue

        # Получить тайтл из карточки
        title = ""
        try:
            card_text = btn.evaluate(
                "el => { let p = el; for(let i=0;i<10;i++){ p=p.parentElement; "
                "if(!p) break; "
                "if(p.tagName==='LI' || p.getAttribute('data-view-name')) return p.innerText; "
                "} return ''; }"
            ) or ""
            lines = [l.strip() for l in card_text.split("\n")
                     if l.strip() and len(l.strip()) > 2
                     and l.strip() not in ("Установить контакт", "Подписаться", "Follow",
                                           "Connect", "Message", "Написать")]
            # Тайтл — строка после имени, не степень связи
            found_name = False
            for line in lines:
                if name.split()[0] in line:
                    found_name = True
                    continue
                if not found_name:
                    continue
                if re.match(r"^(Контакт\s+\d+|1-й|2-й|3-й|\d+(st|nd|rd|th))", line):
                    continue
                title = line
                break
        except Exception:
            pass

        role_type = classify_title(title)
        # Если тайтл не распознан — доверяем ключевому слову поиска
        if role_type == "другое" and not title:
            if any(k in role_kw for k in ["product", "owner", "cpo"]):
                role_type = "продакт"
            elif any(k in role_kw for k in ["recruit", "talent", "hr"]):
                role_type = "рекрутёр"

        if role_type == "другое":
            log.info(f"  ⏭️  {name} — {title or '(нет должности)'}")
            skipped_count += 1
            continue

        log.info(f"\n  👤 {name} | {title or '?'} [{role_type}]")

        seen.add(profile_url)

        try:
            btn.scroll_into_view_if_needed()
            page.wait_for_timeout(400)
            btn.click()
            ok = send_invite_modal(page)
            if ok:
                log.info(f"     ✅ Отправлено | {profile_url or name}")
                sent.append(profile_url or name)
                sent_count += 1
            else:
                log.info(f"     ⚠️  Диалог не найден")
        except Exception as e:
            log.debug(f"click error: {e}")
            log.info(f"     ❌ Ошибка")
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass

        delay = random.uniform(*DELAY_BETWEEN)
        time.sleep(delay)

    return sent, sent_count, skipped_count


def main():
    total_sent = total_skipped = 0
    seen_profiles: set[str] = set()
    all_sent: list[str] = []

    log.info("🤝 LinkedIn Connect — поиск через people search (РФ + КЗ)")
    log.info(f"   Максимум приглашений: {MAX_INVITES_PER_RUN}\n")

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=False)
    ctx = browser.new_context(storage_state=SESSION_FILE)
    page = ctx.new_page()

    # ─── Проверка сессии ───────────────────────────────────────────────────
    try:
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(3000)
    cur = page.url
    if "authwall" in cur or "login" in cur or "feed" not in cur:
        log.error(f"❌ Сессия истекла (URL: {cur}). Запусти python3 linkedin_login.py")
        browser.close(); pw.stop()
        return
    log.info("✅ Авторизация OK\n")

    try:
        for company, role_kw in SEARCHES:
            if total_sent >= MAX_INVITES_PER_RUN:
                break

            url = build_url(company, role_kw)
            log.info(f"\n🔍 «{company}» / «{role_kw}»")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(4000)

            sent, sc, sk = process_search_page(
                page, role_kw, seen_profiles,
                max_left=MAX_INVITES_PER_RUN - total_sent
            )
            total_sent += sc
            total_skipped += sk
            all_sent.extend(sent)

    finally:
        browser.close()
        pw.stop()

    log.info(f"\n{'='*50}")
    log.info(f"📊 Приглашений: {total_sent} | Пропущено: {total_skipped}")
    if all_sent:
        log.info("\n📎 Отправлено:")
        for p in all_sent:
            log.info(f"   {p}")
    log.info(f"{'='*50}")


if __name__ == "__main__":
    main()

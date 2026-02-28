from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state="linkedin_session.json")
    page = context.new_page()
    
    # Прямой URL поиска постов
    page.goto("https://www.linkedin.com/search/results/content/?keywords=ищу продакта&sortBy=%22date_posted%22", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    
    # Скроллим
    for _ in range(3):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)
    
    # Пробуем разные селекторы для постов
    selectors = [
        ".feed-shared-update-v2",
        "[data-urn]",
        ".update-components-text",
        ".feed-shared-text",
        "span.break-words",
        ".occludable-update",
    ]
    
    for sel in selectors:
        els = page.query_selector_all(sel)
        if els:
            print(f"\nСелектор '{sel}': {len(els)} элементов")
            for el in els[:3]:
                txt = el.inner_text().strip()[:200]
                if len(txt) > 20:
                    print(f"  → {txt}")
    
    # Если ничего — снимок текста
    print("\n--- Часть текста страницы ---")
    print(page.inner_text("body")[:3000])
    
    input("\nНажми ENTER...")
    browser.close()

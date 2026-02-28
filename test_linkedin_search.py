from playwright.sync_api import sync_playwright
import time

searches = [
    ("продакт менеджер", "https://www.linkedin.com/jobs/search/?keywords=%D0%BF%D1%80%D0%BE%D0%B4%D0%B0%D0%BA%D1%82%20%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80&f_TPR=r604800"),
    ("product manager Moscow", "https://www.linkedin.com/jobs/search/?keywords=product%20manager&location=Moscow&f_TPR=r604800"),
    ("менеджер продукта", "https://www.linkedin.com/jobs/search/?keywords=%D0%BC%D0%B5%D0%BD%D0%B5%D0%B4%D0%B6%D0%B5%D1%80%20%D0%BF%D1%80%D0%BE%D0%B4%D1%83%D0%BA%D1%82%D0%B0&f_TPR=r604800"),
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state="linkedin_session.json")
    page = context.new_page()
    
    for label, url in searches:
        print(f"\n🔍 «{label}»")
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        
        # Скроллим чтобы подгрузить
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)
        
        cards = page.query_selector_all(".job-card-container, .jobs-search-results__list-item, [data-job-id]")
        print(f"   Карточек: {len(cards)}")
        
        for card in cards[:5]:
            text = card.inner_text().strip().replace("\n", " | ")[:120]
            print(f"   {text}")
        
        if not cards:
            body = page.inner_text("body")[:500]
            print(f"   Текст: {body}")
    
    browser.close()

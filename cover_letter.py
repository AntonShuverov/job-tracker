"""
Генерация сопроводительных писем + ранжирование вакансий по резюме.
Использует Qwen AI для анализа совместимости и генерации писем.
"""

import os
import json
import re
import requests
from dotenv import load_dotenv

load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")
NOTION_HEADERS = {"Authorization": f"Bearer {NOTION_TOKEN}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}

# Загружаем резюме
RESUME_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
with open(RESUME_PATH, "r", encoding="utf-8") as f:
    RESUME_TEXT = f.read()


COVER_LETTER_PROMPT = """Ты — карьерный помощник. Тебе дано резюме кандидата и описание вакансии.

Задачи:
1. Оцени релевантность кандидата для этой вакансии
2. Напиши короткое сопроводительное письмо

Верни JSON:
{
  "relevance": "высокая" или "средняя" или "низкая",
  "relevance_reason": "Почему такая оценка (1 предложение)",
  "cover_letter": "Сопроводительное письмо (3-5 предложений, на языке вакансии)"
}

ПРАВИЛА для сопроводительного:
- Пиши на том же языке, что и вакансия (русский или английский)
- Начни с приветствия и позиции
- Упомяни 2-3 самых релевантных достижения из резюме с цифрами
- Объясни почему кандидат подходит именно на эту роль
- Закончи готовностью обсудить детали
- Если вакансия на русском — "Здравствуйте!". Если на английском — "Hi!". НЕ пиши "Уважаемый HR"
- Тон: профессиональный но живой, без канцелярита
- Максимум 5-6 предложений, компактно

ПРАВИЛА для релевантности:
- "высокая" — опыт напрямую совпадает с требованиями, есть нужные навыки
- "средняя" — частичное совпадение, кандидат может справиться но не идеальный матч
- "низкая" — мало пересечений, другой профиль

Верни ТОЛЬКО JSON."""


def get_new_vacancies():
    """Получает вакансии со статусом 'Новая' из Notion."""
    results = []
    has_more = True
    start_cursor = None

    while has_more:
        payload = {
            "filter": {"property": "Статус", "select": {"equals": "Новая"}},
            "page_size": 100,
        }
        if start_cursor:
            payload["start_cursor"] = start_cursor

        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=NOTION_HEADERS, json=payload, timeout=30)

        if resp.status_code != 200:
            print(f"❌ Notion ошибка: {resp.status_code}")
            break

        data = resp.json()
        results.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    return results


def extract_vacancy_text(page):
    """Извлекает текст вакансии из Notion-страницы."""
    props = page["properties"]

    def get_text(prop_name):
        prop = props.get(prop_name, {})
        if prop.get("type") == "title":
            return "".join(t["plain_text"] for t in prop.get("title", []))
        elif prop.get("type") == "rich_text":
            return "".join(t["plain_text"] for t in prop.get("rich_text", []))
        elif prop.get("type") == "select" and prop.get("select"):
            return prop["select"]["name"]
        elif prop.get("type") == "url":
            return prop.get("url") or ""
        return ""

    parts = []
    title = get_text("Должность")
    if title:
        parts.append(f"Должность: {title}")
    company = get_text("Компания")
    if company:
        parts.append(f"Компания: {company}")
    location = get_text("Локация")
    if location:
        parts.append(f"Локация: {location}")
    schedule = get_text("Формат работы")
    if schedule:
        parts.append(f"Формат: {schedule}")
    salary = get_text("Зарплата")
    if salary:
        parts.append(f"Зарплата: {salary}")
    notes = get_text("Заметки")
    if notes:
        parts.append(f"Требования: {notes}")

    return "\n".join(parts), title, page["id"]


def generate_cover_letter(vacancy_text):
    """Генерирует сопроводительное и оценку через Qwen."""
    prompt = f"{COVER_LETTER_PROMPT}\n\n--- РЕЗЮМЕ ---\n{RESUME_TEXT}\n\n--- ВАКАНСИЯ ---\n{vacancy_text}"

    try:
        resp = requests.post(QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 800},
            timeout=30)
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"Qwen ошибка: {e}")
    return None


def update_notion_page(page_id, cover_letter, relevance):
    """Обновляет вакансию в Notion: сопроводительное + релевантность."""
    relevance_map = {
        "высокая": "🔥 Высокая",
        "средняя": "👍 Средняя",
        "низкая": "🤷 Низкая",
    }
    notion_relevance = relevance_map.get(relevance, "👍 Средняя")

    props = {
        "Сопроводительное письмо": {"rich_text": [{"text": {"content": cover_letter[:2000]}}]},
        "Релевантность": {"select": {"name": notion_relevance}},
    }

    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": props},
        timeout=30)

    return resp.status_code == 200


def main():
    print("📋 Загружаю вакансии со статусом 'Новая'...")
    vacancies = get_new_vacancies()
    print(f"   Найдено: {len(vacancies)} вакансий\n")

    if not vacancies:
        print("Нет новых вакансий!")
        return

    processed = 0
    for page in vacancies:
        vacancy_text, title, page_id = extract_vacancy_text(page)

        print(f"📝 {title}")
        result = generate_cover_letter(vacancy_text)

        if not result:
            print(f"   ⚠️  Не удалось сгенерировать\n")
            continue

        relevance = result.get("relevance", "средняя")
        reason = result.get("relevance_reason", "")
        letter = result.get("cover_letter", "")

        # Добавляем причину в начало письма как комментарий
        full_text = f"[{relevance.upper()}] {reason}\n\n{letter}"

        if update_notion_page(page_id, full_text, relevance):
            emoji = {"высокая": "🔥", "средняя": "👍", "низкая": "🤷"}.get(relevance, "?")
            print(f"   {emoji} {relevance} — обновлено")
        else:
            print(f"   ❌ Ошибка обновления Notion")

        processed += 1

    print(f"\n{'='*50}")
    print(f"📊 Обработано: {processed}/{len(vacancies)} вакансий")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()

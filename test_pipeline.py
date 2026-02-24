"""
JobTracker — тест полного цикла: Текст → Qwen AI → Notion
Без Telegram, вручную вставляем текст вакансии.
"""
import os
import json
import re
import requests
from dotenv import load_dotenv

load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# ─── Qwen AI: разбор вакансии ───

PARSE_PROMPT = """Проанализируй текст сообщения и определи, содержит ли оно вакансию.
Если это НЕ вакансия — верни: {"is_vacancy": false}
Если вакансия, извлеки данные и верни JSON:
{
  "is_vacancy": true,
  "title": "Название должности",
  "company": "Компания или null",
  "schedule": "Один из: Офис / Удалёнка / Гибрид / Не указано",
  "location": "Город или null",
  "salary": "Зарплата или null",
  "contact": "Email, @username, ссылка или null",
  "link": "Ссылка на вакансию или null",
  "notes": "Ключевые требования кратко или null"
}
Верни ТОЛЬКО JSON, без пояснений."""

def parse_vacancy(text):
    resp = requests.post(
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {QWEN_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "qwen-turbo",
            "messages": [
                {"role": "user", "content": f"{PARSE_PROMPT}\n\nТекст:\n\n{text}"}
            ],
            "max_tokens": 500,
        },
    )
    content = resp.json()["choices"][0]["message"]["content"]
    # Извлекаем JSON из ответа
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        return json.loads(match.group())
    return None

# ─── Notion: запись в таблицу ───

def write_to_notion(vacancy, source="Тест"):
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    properties = {
        "Должность": {"title": [{"text": {"content": vacancy.get("title", "?")}}]},
        "Статус": {"select": {"name": "Новая"}},
        "Источник": {"select": {"name": "Telegram"}},
    }
    if vacancy.get("company"):
        properties["Компания"] = {"rich_text": [{"text": {"content": vacancy["company"]}}]}
    if vacancy.get("schedule"):
        valid = ["Офис", "Удалёнка", "Гибрид", "Не указано"]
        sched = vacancy["schedule"]
        if sched in valid:
            properties["Формат работы"] = {"select": {"name": sched}}
    if vacancy.get("location"):
        properties["Локация"] = {"rich_text": [{"text": {"content": vacancy["location"]}}]}
    if vacancy.get("salary"):
        properties["Зарплата"] = {"rich_text": [{"text": {"content": vacancy["salary"]}}]}
    if vacancy.get("contact"):
        properties["Ссылка на HR"] = {"url": vacancy["contact"]} if vacancy["contact"].startswith("http") else None
        if not vacancy["contact"].startswith("http"):
            properties["Заметки"] = {"rich_text": [{"text": {"content": f"Контакт: {vacancy['contact']}"}}]}
    if vacancy.get("link"):
        properties["Ссылка на вакансию"] = {"url": vacancy["link"]}
    if vacancy.get("notes") and "Заметки" not in properties:
        properties["Заметки"] = {"rich_text": [{"text": {"content": vacancy["notes"]}}]}

    # Убираем None значения
    properties = {k: v for k, v in properties.items() if v is not None}

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties},
    )
    return resp.status_code == 200

# ─── Главный цикл ───

if __name__ == "__main__":
    print("=" * 50)
    print("🎯 JobTracker — тест полного цикла")
    print("=" * 50)
    print("\nВставь текст вакансии (или 'q' для выхода).")
    print("После текста нажми Enter дважды.\n")

    while True:
        lines = []
        print("─" * 40)
        print("📝 Текст вакансии:")
        while True:
            line = input()
            if line == "":
                break
            if line.lower() == "q":
                print("👋 Выход!")
                exit()
            lines.append(line)

        text = "\n".join(lines)
        if not text.strip():
            continue

        print("\n🤖 Qwen анализирует...")
        vacancy = parse_vacancy(text)

        if not vacancy or not vacancy.get("is_vacancy"):
            print("❌ Это не вакансия, пропускаю.\n")
            continue

        print(f"✅ Найдена вакансия:")
        print(f"   Должность: {vacancy.get('title')}")
        print(f"   Компания:  {vacancy.get('company')}")
        print(f"   Формат:    {vacancy.get('schedule')}")
        print(f"   Локация:   {vacancy.get('location')}")
        print(f"   Зарплата:  {vacancy.get('salary')}")
        print(f"   Контакт:   {vacancy.get('contact')}")
        print(f"   Ссылка:    {vacancy.get('link')}")

        print("\n📤 Записываю в Notion...")
        if write_to_notion(vacancy):
            print("✅ Записано в Notion! Проверь таблицу.\n")
        else:
            print("❌ Ошибка записи в Notion.\n")

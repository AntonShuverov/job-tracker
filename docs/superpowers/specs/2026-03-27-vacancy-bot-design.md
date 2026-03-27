# Vacancy Bot — Design Spec

**Date:** 2026-03-27
**Status:** Approved

---

## Overview

Новый независимый инструмент: Telegram-бот для ручной модерации вакансий из Telegram-каналов.

**Проблема:** Текущий tg_parser.py автоматически сохраняет всё в Notion без ручного контроля.
**Решение:** Бот перехватывает все сообщения из каналов, показывает пользователю с кнопками "Сохранить / Пропустить", и только после подтверждения запускает Qwen и пишет в Notion.

---

## User Flow

```
Новое сообщение в TG-канале
        │
        ▼
  Telethon listener
        │
        ▼
  SQLite (messages.db)   ← все сообщения сразу
        │
        ▼
  Bot Worker (polling 2с)
        │
        ▼
  Сообщение + кнопки в личку пользователю
        │
     ┌──┴──┐
     ▼     ▼
  Сохранить  Пропустить
     │           │
     ▼           ▼
  Qwen API    удалить из БД
     │         удалить из чата
     ▼
  Notion API
     │
     ▼
  Запись в таблицу
```

---

## Architecture

Один процесс (`vacancy_bot.py`) запускает два asyncio-таска параллельно:

1. **Listener** (Telethon) — подключается к каналам в live-режиме, каждое новое сообщение записывает в SQLite со статусом `pending`.
2. **Bot Worker** (aiogram) — каждые 2 секунды проверяет SQLite на `pending` записи, отправляет пользователю, обновляет статус на `sent`.

При нажатии кнопки:
- **Сохранить** → Qwen парсит текст → Notion API создаёт страницу → статус `saved`
- **Пропустить** → статус `skipped` → сообщение в боте удаляется

---

## Project Structure

```
vacancy_bot/
├── vacancy_bot.py      # Точка входа: запускает listener + bot worker
├── listener.py         # Telethon: слушает каналы → пишет в SQLite
├── bot.py              # aiogram: отправляет сообщения, обрабатывает кнопки
├── db.py               # SQLite CRUD (messages.db)
├── processor.py        # Qwen парсинг + Notion запись
├── .env                # Ключи
└── requirements.txt
```

---

## Database Schema

**Таблица `messages`:**

| Поле            | Тип      | Описание                                    |
|-----------------|----------|---------------------------------------------|
| `id`            | INTEGER  | PRIMARY KEY AUTOINCREMENT                   |
| `channel`       | TEXT     | Название канала                             |
| `text`          | TEXT     | Полный текст сообщения                      |
| `tg_link`       | TEXT     | Ссылка на пост (https://t.me/...)           |
| `status`        | TEXT     | `pending` / `sent` / `saved` / `skipped`   |
| `bot_message_id`| INTEGER  | ID сообщения в боте (для удаления)          |
| `created_at`    | DATETIME | UTC timestamp                               |

---

## Configuration (.env)

```env
# Telethon (чтение каналов)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=

# Telegram Bot
BOT_TOKEN=          # от BotFather
YOUR_CHAT_ID=       # твой Telegram user_id

# Каналы (через запятую)
TG_CHANNELS=channel1,channel2,channel3

# Qwen
QWEN_API_KEY=
QWEN_MODEL=qwen-turbo

# Notion
NOTION_TOKEN=
NOTION_DATABASE_ID=
```

---

## Notion Integration

При нажатии "Сохранить" `processor.py`:
1. Вызывает Qwen с текстом вакансии (`PARSE_PROMPT` из tg_parser.py)
2. Если Qwen вернул `is_vacancy: true` → создаёт страницу в Notion
3. Если Qwen вернул `is_vacancy: false` или ошибку → создаёт страницу с сырым текстом в поле "Заметки", остальные поля пустые
4. Без генерации сопроводительного письма и оценки релевантности в MVP

Notion-поля такие же как в текущем tg_parser.py: Должность, Компания, Статус, Источник, Пост в ТГ, Ссылка на вакансию, Контакт ТГ, Email, Формат работы, Локация, Зарплата, Заметки.

---

## Error Handling

| Ситуация                   | Поведение                                                      |
|----------------------------|----------------------------------------------------------------|
| Telethon FloodWait         | Автоматическое ожидание (встроено в Telethon)                  |
| Qwen недоступен            | Уведомление пользователю в боте, статус остаётся `sent`        |
| Notion API ошибка          | Уведомление пользователю в боте, статус остаётся `sent`        |
| Бот перезапустился         | При старте все `pending`/`sent` записи шлются заново           |
| Дубликат в Notion          | Проверка по tg_link перед записью (как в текущем коде)         |

---

## Startup

```bash
cd vacancy_bot
pip install -r requirements.txt

# Первый запуск — авторизация Telethon (ввести номер и код)
python3 vacancy_bot.py

# Фоновый запуск (после авторизации)
nohup python3 vacancy_bot.py > bot.log 2>&1 &
```

---

## Out of Scope (MVP)

- PM-фильтр (все сообщения идут в бот)
- Генерация сопроводительного письма
- Оценка релевантности
- Веб-интерфейс
- Источники hh.ru и LinkedIn
- Деплой на второй MacBook (следующий шаг после локального теста)

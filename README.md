# 🤖 Job Tracker — AI-Powered Vacancy Parser

> Автоматический сбор вакансий из Telegram, LinkedIn и hh.ru с AI-анализом релевантности и генерацией сопроводительных писем → Notion

![Python](https://img.shields.io/badge/Python-3.13-blue?logo=python&logoColor=white)
![Notion](https://img.shields.io/badge/Notion-API-black?logo=notion&logoColor=white)
![Telegram](https://img.shields.io/badge/Telegram-MTProto-26A5E4?logo=telegram&logoColor=white)
![AI](https://img.shields.io/badge/AI-Qwen_Turbo-orange)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 🎯 Что это

Система автоматического поиска работы. Парсит вакансии из нескольких источников, анализирует их через AI, оценивает релевантность под твоё резюме, генерирует персональные сопроводительные письма и сохраняет всё в Notion. На hh.ru — автоматически откликается.

**Вместо**: ручного просмотра десятков каналов и сайтов каждый день  
**Получаешь**: готовую базу вакансий с оценкой 🔥/👍/🤷 и письмами в один клик

---

## ✨ Возможности

| Функция | Описание |
|---|---|
| 📡 Telegram-каналы | Парсит 7+ каналов с вакансиями, извлекает структурированные данные |
| 💼 LinkedIn посты | Ищет посты "ищу продакта" и аналогичные через Playwright |
| 🏢 hh.ru API | Поиск IT-вакансий по ролям и регионам через официальный API |
| 🤖 AI-анализ | Qwen Turbo извлекает поля, оценивает релевантность, пишет письма |
| ✉️ Автоотклик | Playwright автоматически откликается на hh.ru с письмом |
| 🔍 Дедупликация | По URL — одна вакансия записывается только один раз |
| 📊 Notion база | Все вакансии в одном месте со статусами, фильтрами и сортировкой |

---

## 🏗️ Архитектура

```
┌─────────────────────┐     ┌──────────────────────┐     ┌────────────────┐
│      Источники       │────▶│     Qwen AI Engine    │────▶│     Notion     │
│                     │     │                      │     │                │
│  • ТГ-каналы (7+)   │     │  • Парсинг текста    │     │  • Вакансии    │
│  • LinkedIn посты   │     │  • Извлечение полей  │     │  • Статусы     │
│  • LinkedIn поиск   │     │  • Оценка 🔥/👍/🤷   │     │  • Письма      │
│  • hh.ru API        │     │  • Сопроводительные  │     │  • Контакты    │
│  • Career pages     │     │  • Автоотклик        │     │  • Даты        │
└─────────────────────┘     └──────────────────────┘     └────────────────┘
```

---

## 📁 Структура проекта

```
job_tracker/
├── run.py                # Точка входа: запуск ТГ-парсера
├── tg_parser.py          # Ядро: Telegram + Web scraping → AI → Notion
├── hh_parser.py          # Поиск IT-вакансий на hh.ru → Notion
├── hh_apply.py           # Автоотклик на hh.ru с сопроводительными письмами
├── linkedin_posts.py     # Парсинг постов LinkedIn с вакансиями
├── linkedin_parser.py    # Поиск вакансий на LinkedIn Jobs
├── cover_letter.py       # Догенерация писем для существующих вакансий
├── hh_config.py          # Настройки поиска hh.ru
├── resume.txt            # ❌ не в репо (добавь своё — см. Setup)
├── .env                  # ❌ не в репо (см. .env.example)
├── .env.example          # Шаблон переменных окружения
└── requirements.txt      # Зависимости
```

---

## 🚀 Быстрый старт

### 1. Клонируй репо

```bash
git clone https://github.com/AntonShuverov/job-tracker.git
cd job-tracker
```

### 2. Установи зависимости

```bash
pip install -r requirements.txt
playwright install chromium   # для LinkedIn и hh.ru автоотклика
```

### 3. Настрой окружение

```bash
cp .env.example .env
```

Заполни `.env`:

```env
TELEGRAM_API_ID=ваш_api_id
TELEGRAM_API_HASH=ваш_api_hash
TG_CHANNELS=channel1,channel2,channel3

QWEN_API_KEY=ваш_ключ_qwen
QWEN_MODEL=qwen-turbo

NOTION_TOKEN=ваш_integration_token
NOTION_DATABASE_ID=id_вашей_базы
```

### 4. Добавь резюме

Создай файл `resume.txt` с текстом своего резюме — именно под него AI будет оценивать релевантность и писать сопроводительные письма.

### 5. Запускай

```bash
# Telegram-каналы (batch)
python3 run.py

# Telegram live-мониторинг
python3 run.py --live

# hh.ru — сбор вакансий
python3 hh_parser.py

# hh.ru — автоотклик
python3 hh_apply.py

# LinkedIn посты (только локально, не на сервере в РФ)
python3 linkedin_posts.py

# Догенерация писем для уже собранных вакансий
python3 cover_letter.py
```

---

## ⚙️ Настройка

### Telegram-каналы

В `.env` через запятую:
```env
TG_CHANNELS=products_jobs_projects,forproducts,hireproproduct,evacuatejobs
```

### Лимиты и фильтры (в файлах)

| Параметр | Файл | По умолчанию |
|---|---|---|
| `INITIAL_MESSAGES_LIMIT` | `tg_parser.py` | `20` |
| `MAX_APPLIES` | `hh_apply.py` | `200` |
| `DATE_FROM_DAYS` | `hh_apply.py` / `hh_parser.py` | `60` |
| `PAGES` | `hh_apply.py` | `3` |

### PM-фильтр

Система записывает только продуктовые роли. Ключевые слова (`PM_KEYWORDS`):
```
product, продукт, продакт, cpo, chief product, head of product,
product analyst, продуктовый аналитик, аналитик продукта, product owner
```

---

## 🗄️ Notion — структура базы

Создай базу данных со следующими полями:

| Поле | Тип | Описание |
|---|---|---|
| Должность | Title | Название вакансии |
| Компания | Text | Название компании |
| Статус | Select | Новая / Отправлено / Отказ / Оффер |
| Релевантность | Select | 🔥 Высокая / 👍 Средняя / 🤷 Низкая |
| Источник | Select | Telegram / LinkedIn / hh.ru |
| Формат работы | Select | Офис / Удалёнка / Гибрид |
| Локация | Text | Город |
| Зарплата | Text | Вилка |
| Ссылка на вакансию | URL | Ссылка на вакансию |
| Сопроводительное письмо | Text | AI-письмо |
| Заметки | Text | Требования кратко |
| ТГ-канал | Text | Откуда |
| Дата отправления | Date | Когда откликнулся |

---

## 🔑 Получение ключей

### Telegram API
1. Перейди на [my.telegram.org](https://my.telegram.org)
2. Войди и открой "API development tools"
3. Создай приложение → скопируй `api_id` и `api_hash`

### Qwen API (Alibaba Cloud)
1. Зарегистрируйся на [dashscope.aliyuncs.com](https://dashscope.aliyuncs.com)
2. Создай API-ключ в разделе "API Keys"
3. Используй endpoint: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`

### Notion Integration
1. Открой [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Создай новую интеграцию → скопируй `Internal Integration Token`
3. В своей базе данных: ··· → Connections → подключи интеграцию
4. Скопируй ID базы из URL: `notion.so/workspace/{DATABASE_ID}?v=...`

---

## 🖥️ Деплой на сервер (VPS)

```bash
# Клонируй на сервер
git clone https://github.com/AntonShuverov/job-tracker.git /root/job-tracker
cd /root/job-tracker
pip install -r requirements.txt

# Настрой .env и resume.txt

# Запуск в фоне
nohup python3 run.py > last_run.log 2>&1 &
nohup python3 hh_parser.py >> last_run.log 2>&1 &
```

### Cron (автозапуск 2 раза в день)

```bash
crontab -e
```

```cron
0 6 * * * cd /root/job-tracker && python3 run.py >> cron.log 2>&1
0 15 * * * cd /root/job-tracker && python3 hh_parser.py >> cron.log 2>&1
```

> ⚠️ **LinkedIn** работает только локально — сервер в РФ, LinkedIn заблокирован.

---

## 🧩 Технологии

| Компонент | Технология |
|---|---|
| Язык | Python 3.13 |
| Telegram | Telethon (MTProto API) |
| LinkedIn / hh.ru автоотклик | Playwright (headless Chromium) |
| Web scraping | BeautifulSoup + lxml |
| AI-анализ | Qwen API (qwen-turbo) |
| База данных | Notion API |
| Хостинг | Timeweb Cloud VPS (Ubuntu 24.04) |

---

## 📝 Лицензия

MIT — используй свободно, форкай, улучшай.

---

<div align="center">
  <sub>Сделано для автоматизации поиска работы 🚀</sub>
</div>

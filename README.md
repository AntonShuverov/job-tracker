# 🎯 Job Tracker

Система автоматического сбора вакансий → AI-анализ → Notion → автоотклик

---

## Что делает

1. **Собирает** вакансии из Telegram-каналов, LinkedIn и hh.ru
2. **Анализирует** через Qwen AI — извлекает должность, компанию, зарплату, формат работы
3. **Оценивает релевантность** 🔥 / 👍 / 🤷 по твоему резюме
4. **Генерирует сопроводительное письмо** с цифрами из резюме под каждую вакансию
5. **Сохраняет в Notion** с дедупликацией по URL
6. **Автоматически откликается** на hh.ru
7. **Нетворкинг в LinkedIn** — автоматически отправляет приглашения продактам и рекрутёрам в IT-компаниях РФ и КЗ

---

## Скрипты

| Скрипт | Что делает |
|---|---|
| `run.py` | Парсинг Telegram-каналов → Notion |
| `hh_apply.py` | Поиск вакансий на hh.ru + автоотклик с AI-письмом |
| `hh_parser.py` | Поиск вакансий на hh.ru → Notion (без отклика) |
| `linkedin_posts.py` | Парсинг LinkedIn постов → Notion |
| `linkedin_connect.py` | Автоматические приглашения в контакты LinkedIn (продакты + рекрутёры, РФ + КЗ) |
| `cover_letter.py` | Догенерация писем для вакансий уже в Notion |

> ⚠️ LinkedIn работает только с зарубежного сервера или через VPN — в РФ заблокирован.

---

## Установка

```bash
git clone https://github.com/AntonShuverov/job-tracker.git
cd job-tracker

pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Заполни .env своими ключами
```

### .env

```env
TELEGRAM_API_ID=        # https://my.telegram.org
TELEGRAM_API_HASH=
QWEN_API_KEY=           # https://dashscope.aliyuncs.com
NOTION_TOKEN=           # https://www.notion.so/my-integrations
NOTION_DATABASE_ID=
TG_CHANNELS=channel1,channel2,channel3
```

### Резюме

Создай файл `resume.txt` в корне проекта — AI будет использовать его для оценки релевантности и генерации писем. Формат свободный.

---

## Авторизация (один раз)

```bash
# Telegram — введи номер и код
python3 run.py

# hh.ru — откроется браузер, войди вручную
python3 hh_login.py

# LinkedIn — откроется браузер, войди вручную (только с зарубежного сервера / VPN)
python3 linkedin_login.py

# Сессия сохраняется в linkedin_session.json (~1 неделя)
```

---

## Запуск

```bash
# Telegram
python3 run.py

# hh.ru — поиск + автоотклик
python3 hh_apply.py

# LinkedIn посты → Notion
python3 linkedin_posts.py

# LinkedIn нетворкинг — отправить приглашения (до 40-50 в день)
python3 linkedin_connect.py

# Фоновый запуск
nohup python3 run.py > run.log 2>&1 &
```

---

## Стек

| | |
|---|---|
| Telegram | Telethon (MTProto) |
| LinkedIn / hh.ru отклик | Playwright (headless Chromium) |
| hh.ru поиск | hh.ru REST API |
| AI | Qwen API (qwen-turbo) |
| База данных | Notion API |

---

## Структура

```
├── run.py                  # Точка входа: Telegram
├── tg_parser.py            # Парсинг ТГ + AI + Notion
├── hh_apply.py             # Автоотклик hh.ru
├── hh_parser.py            # Поиск hh.ru → Notion
├── linkedin_posts.py       # LinkedIn посты → Notion
├── linkedin_connect.py     # LinkedIn нетворкинг — авто-приглашения
├── cover_letter.py         # Догенерация писем
│
├── resume.txt              # ❌ не в Git
├── .env                    # ❌ не в Git
├── hh_session.json         # ❌ не в Git
├── linkedin_session.json   # ❌ не в Git  (обновлять через linkedin_login.py ~каждую неделю)
│
├── .env.example
├── requirements.txt
└── README.md
```

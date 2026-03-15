# LinkedIn Content Publisher — Design Spec
**Date:** 2026-03-15
**Status:** Approved

## Overview

Two standalone scripts for publishing LinkedIn posts from a Notion content calendar and collecting analytics. Completely separate from existing tools (`linkedin_posts.py`, `linkedin_connect.py`, `linkedin_parser.py`).

## Architecture

### Files

```
linkedin_publisher.py   — publishes scheduled posts from Notion to LinkedIn
linkedin_analytics.py   — collects analytics from published posts back into Notion
```

Both scripts reuse:
- `common.py` — `get_notion_headers()`, logging setup
- `linkedin_session.json` — existing Playwright browser session

### Environment Variables

Add to `.env` and `.env.example`:
```
LINKEDIN_CONTENT_DB_ID=9836eeef5b1a474b9d9759110f5b9988
```

---

## Notion Database: "✍️ LinkedIn — Посты"

**ID:** `9836eeef5b1a474b9d9759110f5b9988`

| Field | Type | Role |
|-------|------|------|
| `Заголовок` | title | Post title (for reference) |
| `Текст поста` | rich_text | Post body text to publish |
| `Статус` | select | Workflow state (see below) |
| `Дата когда нужно опубликовать ` | date | Scheduled publish date (set by user); note: trailing space in field name |
| `Дата публикации` | date | Actual publish date (set by script) |
| `Ссылка` | url | LinkedIn post URL (set by script) |
| `Язык` | select | Post language (informational) |
| `Теги` | multi_select | Tags (informational) |
| `👍 Реакции` | number | Analytics: reactions |
| `💬 Комментарии` | number | Analytics: comments |
| `🔄 Репосты` | number | Analytics: reposts |
| `👀 Просмотры` | number | Analytics: views |
| `👥 Подписчики+` | number | Analytics: best-effort, likely unavailable from post page |
| `Выводы` | rich_text | Used for error messages and user notes |
| `Зашёл?` | select | User assessment |

### Reading `Текст поста` from Notion API

The `Текст поста` field is a `rich_text` array. To get the full text, concatenate `plain_text` across all items:
```python
text = "".join(item["plain_text"] for item in props["Текст поста"]["rich_text"])
```

### Status Workflow

```
Черновик  →  [user sets date + changes status]  →  Запланирован
Запланирован  →  [linkedin_publisher.py runs]   →  Опубликован | Ошибка
```

**User** controls: Черновик → Запланирован
**Script** controls: Запланирован → Опубликован / Ошибка

---

## linkedin_publisher.py

### Logic

1. Query Notion: `Статус = "Запланирован"` AND `Дата когда нужно опубликовать ` ≤ today (compare as ISO date strings, use local machine date)
2. For each post:
   a. Launch Playwright with `linkedin_session.json`, **`headless=False`** (to avoid bot detection on content creation)
   b. Verify session: navigate to `/feed/`, check URL is not `authwall`/`login`; if expired → log error, exit, tell user to run `linkedin_login.py`
   c. Validate text length: if `Текст поста` > 3000 characters → set `Статус = "Ошибка"`, write error to `Выводы`, log warning, skip to next post
   d. Click "Написать пост" / "Start a post" button on feed page
   e. Type post text into the editor
   f. Click "Опубликовать" / "Post" button
   g. **Capture post URL** (see strategy below)
   h. Update Notion record:
      - `Статус` → `"Опубликован"`
      - `Дата публикации` → today (ISO date: `YYYY-MM-DD`)
      - `Ссылка` → captured URL (or empty string if not captured — do NOT set Ошибка for missing URL alone)

### Post URL Capture Strategy

After clicking "Опубликовать", LinkedIn briefly shows a success notification. Strategy:

1. Navigate to the author's profile recent activity: `https://www.linkedin.com/in/me/recent-activity/all/`
2. Wait for page to load, take the `href` of the first post link (`a[href*="activity"]`)
3. That is the URL of the just-published post

**Fallback:** if URL cannot be captured after 10 seconds, write `""` to `Ссылка`, set status to `"Опубликован"` anyway, log a warning. The post was published — only the URL is missing.

### Error Handling

| Situation | Action |
|-----------|--------|
| Session expired | Log error, exit, print "run linkedin_login.py" |
| Text > 3000 chars | Set `Статус = "Ошибка"`, write reason to `Выводы`, skip |
| Post UI not found | Set `Статус = "Ошибка"`, write reason to `Выводы`, skip |
| URL capture fails | Set `Статус = "Опубликован"`, `Ссылка = ""`, write "URL не захвачен" to `Выводы`, log warning |
| No posts scheduled | Log info "No scheduled posts for today", exit cleanly |

### Logging

Log file: `linkedin_publisher.log`
Both `StreamHandler` (console) and `FileHandler` (file) — matching `linkedin_connect.py` pattern.

---

## linkedin_analytics.py

### Logic

1. Query Notion: `Статус = "Опубликован"` AND `Ссылка` ≠ empty
2. For each post:
   a. Open post URL via Playwright (`headless=True`)
   b. Wait for page to load
   c. Parse from page: reactions, comments, reposts, views
   d. **If a metric is not found on page → leave existing Notion value unchanged** (do not overwrite with 0)
   e. Update only the metrics that were successfully parsed
   f. Sleep `random.uniform(3, 7)` seconds between posts

### Metrics Parsing Notes

- `👥 Подписчики+` is **not available** on individual post pages (only in LinkedIn Creator Analytics). Leave this field for manual input.
- Views (`👀 Просмотры`) may not be visible on all posts (only for posts with enough reach). Skip if not found.

### Error Handling

- Post URL 404 / access denied → log warning, skip
- Session expired → log error, exit

### Logging

Log file: `linkedin_analytics.log`
Both `StreamHandler` and `FileHandler`.

---

## Data Flow Summary

```
User writes post in Notion (Черновик)
  → sets "Дата когда нужно опубликовать" + status "Запланирован"
  → runs: python3 linkedin_publisher.py
  → post published to LinkedIn
  → Notion updated: status "Опубликован", publish date, post URL

24–48 hours later (for meaningful data):
  → runs: python3 linkedin_analytics.py
  → Notion updated: reactions, comments, reposts, views
```

---

## Out of Scope

- Comments on other people's posts (separate tool, future)
- Automatic scheduling / cron setup (user runs manually)
- AI-generated post text
- Image/video attachments (text-only for now)
- `👥 Подписчики+` automation (available only in Creator Analytics, not on post page)

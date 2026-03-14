# Bugfix + Common Module Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 13 bugs and extract duplicated code into `common.py` to eliminate copy-paste across 6 parser files.

**Architecture:** Create a single `common.py` with shared utilities (AI calls, Notion headers, URL normalization, resume loading). Each parser imports from common instead of defining its own copy. Config files (requirements.txt, .env.example, .gitignore) are updated to reflect actual dependencies.

**Tech Stack:** Python 3.11+, requests, python-dotenv, playwright, telethon, beautifulsoup4

---

### Task 1: Create `common.py` with shared utilities

**Files:**
- Create: `common.py`

**Step 1: Create common.py with all shared code**

```python
"""Shared utilities for all job tracker parsers."""

import os
import re
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-turbo")
QWEN_API_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "")

RELEVANCE_MAP = {
    "высокая": "🔥 Высокая",
    "средняя": "👍 Средняя",
    "низкая": "🤷 Низкая",
}

SCHEDULE_MAP = {
    "офис": "Офис", "office": "Офис", "fullDay": "Офис",
    "удалёнка": "Удалёнка", "удаленка": "Удалёнка", "remote": "Удалёнка",
    "гибрид": "Гибрид", "hybrid": "Гибрид", "flexible": "Гибрид",
    "flyInFlyOut": "Офис", "shift": "Офис",
}
VALID_SCHEDULES = {"Офис", "Удалёнка", "Гибрид", "Не указано"}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESUME_PATH = os.path.join(_BASE_DIR, "resume.txt")

logger = logging.getLogger("job_tracker")


def load_resume() -> str:
    if not os.path.exists(RESUME_PATH):
        logger.warning("resume.txt not found")
        return ""
    with open(RESUME_PATH, encoding="utf-8") as f:
        return f.read()


def get_notion_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('NOTION_TOKEN', '')}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


def normalize_url(url: str) -> str:
    if not url:
        return ""
    return url.split("?")[0].split("#")[0].rstrip("/").lower()


def call_qwen(prompt: str, max_tokens: int = 800) -> dict | None:
    try:
        resp = requests.post(
            QWEN_API_URL,
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={"model": QWEN_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.error(f"Qwen API {resp.status_code}")
            return None
        content = resp.json()["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        logger.error(f"Qwen: {e}")
    return None
```

**Step 2: Verify import works**

Run: `cd /Users/anton/job_tracker && python3 -c "from common import call_qwen, get_notion_headers, normalize_url, load_resume, RELEVANCE_MAP; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add common.py
git commit -m "feat: add common.py with shared utilities"
```

---

### Task 2: Fix `hh_parser.py` — use common + fix bugs

**Files:**
- Modify: `hh_parser.py`

**Step 1: Replace local dupes with common imports**

At the top of `hh_parser.py`, replace the local definitions of `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_API_URL`, `RELEVANCE_MAP`, `RESUME_PATH`, `RESUME_TEXT`, `call_qwen`, `normalize_url`, `get_notion_headers` with imports from common:

```python
from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP,
)
```

Remove the deleted local functions/constants. Keep `RESUME_TEXT = load_resume()` as module-level.

**Step 2: Fix bare excepts**

In `get_vacancy_details()` (around line 87), change:
```python
    except: pass
```
to:
```python
    except Exception as e:
        logger.error(f"get_vacancy_details: {e}")
```

In `create_notion_page()` (around line 163), change:
```python
    except: return False
```
to:
```python
    except Exception as e:
        logger.error(f"create_notion_page: {e}")
        return False
```

**Step 3: Fix dedup pagination**

Replace `check_duplicate_by_url()` with paginated version:

```python
def check_duplicate_by_url(hh_url):
    norm = normalize_url(hh_url)
    if not norm:
        return False
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=get_notion_headers(), json=body, timeout=15,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            for page in data.get("results", []):
                db_url = normalize_url(
                    page.get("properties", {}).get("Ссылка на вакансию", {}).get("url") or ""
                )
                if db_url and db_url == norm:
                    return True
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        except Exception as e:
            logger.error(f"check_duplicate_by_url: {e}")
            return False
    return False
```

**Step 4: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "import hh_parser; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add hh_parser.py
git commit -m "fix: hh_parser — use common, fix bare excepts, paginate dedup"
```

---

### Task 3: Fix `hh_apply.py` — use common + fix bugs

**Files:**
- Modify: `hh_apply.py`

**Step 1: Import from common, fix NOTION_HEADERS and resume loading**

At the top, add:
```python
from common import (
    get_notion_headers, normalize_url, load_resume, call_qwen,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, RELEVANCE_MAP,
)
```

Remove local definitions of: `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_API_URL`, `RELEVANCE_MAP`, `_norm_url`, `NOTION_HEADERS` (the constant).

Replace:
```python
RESUME_TEXT = ""
if os.path.exists(RESUME_PATH):
    RESUME_TEXT = open(RESUME_PATH, encoding="utf-8").read()
```
with:
```python
RESUME_TEXT = load_resume()
```

Remove local `RESUME_PATH` definition (imported via common).

**Step 2: Replace all `NOTION_HEADERS` usages with `get_notion_headers()`**

In `notion_is_duplicate()`: change `headers=NOTION_HEADERS` to `headers=get_notion_headers()`
In `notion_save()`: change `headers=NOTION_HEADERS` to `headers=get_notion_headers()`

Replace `_norm_url(url)` calls with `normalize_url(url)`.

Keep `ai_analyze()`, `ai_answer()`, `fmt_salary()`, `strip_html()` as local functions (they have specific signatures).

**Step 3: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "import hh_apply; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add hh_apply.py
git commit -m "fix: hh_apply — use common, fix NOTION_HEADERS constant bug"
```

---

### Task 4: Fix `linkedin_parser.py` — use common + fix dedup

**Files:**
- Modify: `linkedin_parser.py`

**Step 1: Import from common**

Add:
```python
from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP,
)
```

Remove local definitions of: `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_API_URL`, `NOTION_HEADERS`, `RELEVANCE_MAP`, `call_qwen`, `RESUME_PATH`, `RESUME_TEXT` loading block.

Add: `RESUME_TEXT = load_resume()`

**Step 2: Fix dedup — replace title-based with URL-based**

Replace `check_duplicate(title)` with:

```python
def check_duplicate(job_url):
    norm = normalize_url(job_url)
    if not norm:
        return False
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=get_notion_headers(), json=body, timeout=15,
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            for page in data.get("results", []):
                db_url = normalize_url(
                    page.get("properties", {}).get("Ссылка на вакансию", {}).get("url") or ""
                )
                if db_url and db_url == norm:
                    return True
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        except Exception:
            return False
    return False
```

Update the call site (around line 238) — change `check_duplicate(title)` to `check_duplicate(clean_url)`.

**Step 3: Replace NOTION_HEADERS usages**

In `create_notion_page()`: change `headers=NOTION_HEADERS` to `headers=get_notion_headers()`.

**Step 4: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "import linkedin_parser; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add linkedin_parser.py
git commit -m "fix: linkedin_parser — use common, fix dedup by URL instead of title"
```

---

### Task 5: Fix `linkedin_posts.py` — use common

**Files:**
- Modify: `linkedin_posts.py`

**Step 1: Import from common**

Add:
```python
from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP,
)
```

Remove local: `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_API_URL`, `RELEVANCE_MAP`, `call_qwen`, `normalize_url`, `get_notion_headers`, `RESUME_PATH`, and the `open()` line.

Add: `RESUME_TEXT = load_resume()`

**Step 2: Replace `get_notion_headers()` and `normalize_url()` calls**

These now come from common — just delete the local definitions. All call sites remain the same.

**Step 3: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "import linkedin_posts; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add linkedin_posts.py
git commit -m "fix: linkedin_posts — use common, fix unclosed file handle"
```

---

### Task 6: Fix `cover_letter.py` — use common + fix crash

**Files:**
- Modify: `cover_letter.py`

**Step 1: Import from common**

Add:
```python
from common import (
    call_qwen, get_notion_headers, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP,
)
```

Remove local: `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_API_URL`, `NOTION_TOKEN`, `NOTION_HEADERS`, `RESUME_PATH`, and the `with open(RESUME_PATH)` block that crashes.

Add: `RESUME_TEXT = load_resume()`

**Step 2: Replace NOTION_HEADERS usages with get_notion_headers()**

In `get_new_vacancies()`: `headers=NOTION_HEADERS` → `headers=get_notion_headers()`
In `update_notion_page()`: `headers=NOTION_HEADERS` → `headers=get_notion_headers()`

Remove local `relevance_map` dict in `update_notion_page()` — use `RELEVANCE_MAP` from common.

**Step 3: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "import cover_letter; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add cover_letter.py
git commit -m "fix: cover_letter — use common, fix crash when resume.txt missing"
```

---

### Task 7: Fix `tg_parser.py` — use common

**Files:**
- Modify: `tg_parser.py`

**Step 1: Import from common**

Add:
```python
from common import (
    call_qwen, get_notion_headers, normalize_url, load_resume,
    QWEN_API_KEY, QWEN_MODEL, QWEN_API_URL, NOTION_DATABASE_ID,
    RELEVANCE_MAP, VALID_SCHEDULES, SCHEDULE_MAP,
)
```

Remove local: `QWEN_API_KEY`, `QWEN_MODEL`, `QWEN_API_URL`, `NOTION_DATABASE_ID`, `RELEVANCE_MAP`, `SCHEDULE_MAP`, `VALID_SCHEDULES`, `call_qwen`, `normalize_url`, `get_notion_headers`, `RESUME_PATH`, and the resume loading block.

Add: `RESUME_TEXT = load_resume()`

**Step 2: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "import tg_parser; print('OK')"`
Expected: `OK` (may warn about missing Telegram env vars, that's fine)

**Step 3: Commit**

```bash
git add tg_parser.py
git commit -m "refactor: tg_parser — use common, remove duplicated code"
```

---

### Task 8: Fix `linkedin_login.py` — absolute path

**Files:**
- Modify: `linkedin_login.py`

**Step 1: Fix relative path**

Change line 17:
```python
    context.storage_state(path="linkedin_session.json")
```
to:
```python
    import os as _os
    _session = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "linkedin_session.json")
    context.storage_state(path=_session)
```

Actually, cleaner approach — add at top of file:

```python
import os
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "linkedin_session.json")
```

And change `path="linkedin_session.json"` to `path=SESSION_FILE`.

**Step 2: Verify**

Run: `cd /Users/anton/job_tracker && python3 -c "from linkedin_login import SESSION_FILE; print(SESSION_FILE)"`
Expected: absolute path ending in `linkedin_session.json`

**Step 3: Commit**

```bash
git add linkedin_login.py
git commit -m "fix: linkedin_login — use absolute path for session file"
```

---

### Task 9: Delete `hh_config.py`

**Files:**
- Delete: `hh_config.py`

**Step 1: Delete dead file**

```bash
rm hh_config.py
```

**Step 2: Commit**

```bash
git add -u hh_config.py
git commit -m "chore: remove dead hh_config.py (never imported)"
```

---

### Task 10: Fix config files

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `.gitignore`

**Step 1: Update requirements.txt**

```
requests>=2.31.0
python-dotenv>=1.0.0
telethon>=1.36.0
playwright>=1.40.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
```

**Step 2: Update .env.example**

```
QWEN_API_KEY=your_qwen_key_here
QWEN_MODEL=qwen-turbo
NOTION_TOKEN=your_notion_token_here
NOTION_DATABASE_ID=your_database_id_here
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash
TG_CHANNELS=channel1,channel2,channel3
INITIAL_MESSAGES_LIMIT=200
DATE_DAYS=14
MODE=batch
```

**Step 3: Update .gitignore — add log files and telethon session**

Append:
```
*.log
job_tracker_session*
```

**Step 4: Verify**

Run: `cd /Users/anton/job_tracker && cat requirements.txt && echo "---" && cat .env.example && echo "---" && tail -5 .gitignore`
Expected: all three files updated correctly.

**Step 5: Commit**

```bash
git add requirements.txt .env.example .gitignore
git commit -m "fix: update requirements, .env.example, .gitignore for actual deps"
```

---

### Task 11: Smoke test all imports

**Step 1: Test all modules import cleanly**

```bash
cd /Users/anton/job_tracker && python3 -c "
import common; print('common OK')
import hh_parser; print('hh_parser OK')
import hh_apply; print('hh_apply OK')
import linkedin_parser; print('linkedin_parser OK')
import linkedin_posts; print('linkedin_posts OK')
import cover_letter; print('cover_letter OK')
print('ALL OK')
"
```

Expected: all print OK (tg_parser may warn about missing TG env vars).

```bash
cd /Users/anton/job_tracker && python3 -c "import tg_parser; print('tg_parser OK')"
```

Expected: OK or warning about missing env vars (not a crash).

**Step 2: If any fail, fix and recommit**

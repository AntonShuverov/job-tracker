# Bugfix + Common Module Design

## Goal

Fix 13 bugs and extract duplicated code into `common.py` to reduce copy-paste across 6 files.

## New file: `common.py`

Shared utilities extracted from all parsers:

- `call_qwen(prompt, max_tokens=800)` — single AI call function
- `get_notion_headers()` — via function, not module-level constant
- `normalize_url(url)` — URL dedup helper
- `load_resume()` — safe resume.txt loading (with statement, exists check)
- `RELEVANCE_MAP` — shared dict
- `QWEN_API_URL`, `QWEN_API_KEY`, `QWEN_MODEL` — env-based constants

## Bugfixes by file

### hh_parser.py
- Bare `except: pass` (lines 87, 163) -> log errors
- `open()` without `with` (line 26) -> `load_resume()`
- Dedup without pagination (lines 53-65) -> add `has_more`/`next_cursor`
- Import shared functions from common

### hh_apply.py
- `open()` without `with` (line 72) -> `load_resume()`
- `NOTION_HEADERS` as constant (line 208) -> `get_notion_headers()`
- Import shared functions from common

### linkedin_parser.py
- `NOTION_HEADERS` as constant (line 50) -> `get_notion_headers()`
- Dedup by title (lines 95-104) -> dedup by URL
- Import shared functions from common

### linkedin_posts.py
- `open()` without `with` (line 22) -> `load_resume()`
- Import shared functions from common

### cover_letter.py
- `NOTION_HEADERS` as constant (line 19) -> `get_notion_headers()`
- Crash if no resume.txt (lines 23-24) -> `load_resume()`
- Import shared functions from common

### tg_parser.py
- Import shared functions from common (remove local dupes)

### linkedin_login.py
- Relative session path (line 17) -> absolute via `__file__`

## Config fixes

- **requirements.txt**: add `playwright`, `beautifulsoup4`, `lxml`
- **.env.example**: add `QWEN_MODEL`, `TG_CHANNELS`, `INITIAL_MESSAGES_LIMIT`, `DATE_DAYS`, `MODE`
- **.gitignore**: add `*.log`, `job_tracker_session*`

## Delete

- `hh_config.py` — dead code, never imported

## Not touched

- hh script logic (working)
- Test files (in .gitignore)
- Prompts (intentionally different per file)
- LinkedIn CSS selectors (separate task)

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
  urn         TEXT PRIMARY KEY,
  url         TEXT NOT NULL,
  author      TEXT,
  text        TEXT NOT NULL,
  query       TEXT,
  first_seen  TEXT NOT NULL,
  is_vacancy  INTEGER,
  title       TEXT,
  company     TEXT,
  location    TEXT,
  salary      TEXT,
  contact     TEXT,
  notes       TEXT,
  status      TEXT CHECK (status IN ('new','commented','applied','rejected','skipped')),
  updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS comments (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  urn         TEXT NOT NULL REFERENCES posts(urn),
  text        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('published','failed')),
  error       TEXT,
  created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS one_published_comment_per_post
  ON comments(urn) WHERE status = 'published';
CREATE TABLE IF NOT EXISTS actions (
  date   TEXT NOT NULL,
  kind   TEXT NOT NULL,
  count  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (date, kind)
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def open_db(db_path: Path | str):
    conn = connect(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

import sqlite3
from datetime import datetime

from .db import now_iso

VACANCY_STATUSES = ("new", "commented", "applied", "rejected", "skipped")


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def insert_posts(conn: sqlite3.Connection, posts: list[dict], query: str) -> tuple[list[dict], int]:
    new, seen = [], 0
    ts = now_iso()
    for p in posts:
        cur = conn.execute(
            "INSERT OR IGNORE INTO posts (urn, url, author, text, query, first_seen) VALUES (?, ?, ?, ?, ?, ?)",
            (p["urn"], p["url"], p.get("author"), p["text"], query, ts),
        )
        if cur.rowcount:
            new.append(p)
        else:
            seen += 1
    return new, seen


def get_post(conn, urn: str) -> dict | None:
    row = conn.execute("SELECT * FROM posts WHERE urn = ?", (urn,)).fetchone()
    return dict(row) if row else None


def update_post_text(conn, urn: str, text: str) -> None:
    conn.execute("UPDATE posts SET text = ?, updated_at = ? WHERE urn = ?", (text, now_iso(), urn))


def list_unreviewed(conn, limit: int = 50) -> list[dict]:
    return _rows(conn.execute(
        "SELECT urn, url, author, text, first_seen FROM posts WHERE is_vacancy IS NULL "
        "ORDER BY first_seen, rowid LIMIT ?", (limit,)))


def save_vacancy(conn, urn: str, title: str, company: str | None = None, location: str | None = None,
                 salary: str | None = None, contact: str | None = None, notes: str | None = None) -> bool:
    cur = conn.execute(
        "UPDATE posts SET is_vacancy = 1, title = ?, company = ?, location = ?, salary = ?, contact = ?, "
        "notes = ?, status = COALESCE(status, 'new'), updated_at = ? WHERE urn = ?",
        (title, company, location, salary, contact, notes, now_iso(), urn),
    )
    return cur.rowcount > 0


def mark_not_vacancy(conn, urns: list[str]) -> int:
    ts = now_iso()
    return sum(conn.execute("UPDATE posts SET is_vacancy = 0, updated_at = ? WHERE urn = ?", (ts, u)).rowcount
               for u in urns)


def list_vacancies(conn, status: str | None = None, limit: int = 50) -> list[dict]:
    return _rows(conn.execute(
        """
        SELECT p.urn, p.url, p.author, p.title, p.company, p.location, p.salary, p.contact, p.notes,
               p.status, p.first_seen, p.updated_at,
               (SELECT c.text FROM comments c WHERE c.urn = p.urn AND c.status = 'published'
                 ORDER BY c.id DESC LIMIT 1) AS comment,
               (SELECT c.created_at FROM comments c WHERE c.urn = p.urn AND c.status = 'published'
                 ORDER BY c.id DESC LIMIT 1) AS commented_at
        FROM posts p
        WHERE p.is_vacancy = 1 AND (? IS NULL OR p.status = ?)
        ORDER BY p.first_seen DESC, p.rowid DESC
        LIMIT ?
        """, (status, status, limit)))


def set_status(conn, urn: str, status: str) -> bool:
    if status not in VACANCY_STATUSES:
        raise ValueError(f"status must be one of {VACANCY_STATUSES}")
    cur = conn.execute("UPDATE posts SET status = ?, updated_at = ? WHERE urn = ? AND is_vacancy = 1",
                       (status, now_iso(), urn))
    return cur.rowcount > 0


def mark_commented(conn, urn: str) -> None:
    conn.execute("UPDATE posts SET status = 'commented', updated_at = ? WHERE urn = ? AND status = 'new'",
                 (now_iso(), urn))


def add_comment(conn, urn: str, text: str, status: str, error: str | None = None) -> int:
    cur = conn.execute("INSERT INTO comments (urn, text, status, error, created_at) VALUES (?, ?, ?, ?, ?)",
                       (urn, text, status, error, now_iso()))
    return cur.lastrowid


def has_published_comment(conn, urn: str) -> bool:
    return conn.execute("SELECT 1 FROM comments WHERE urn = ? AND status = 'published'", (urn,)).fetchone() is not None


UNCERTAIN_ERRORS = ("not_confirmed", "browser_error")


def has_uncertain_attempt(conn, urn: str) -> bool:
    placeholders = ",".join("?" * len(UNCERTAIN_ERRORS))
    return conn.execute(
        f"SELECT 1 FROM comments WHERE urn = ? AND status = 'failed' AND error IN ({placeholders})",
        (urn, *UNCERTAIN_ERRORS),
    ).fetchone() is not None


def last_published_at(conn) -> datetime | None:
    row = conn.execute("SELECT MAX(created_at) AS ts FROM comments WHERE status = 'published'").fetchone()
    return datetime.fromisoformat(row["ts"]) if row and row["ts"] else None


def last_attempt_at(conn) -> datetime | None:
    row = conn.execute("SELECT MAX(created_at) AS ts FROM comments").fetchone()
    return datetime.fromisoformat(row["ts"]) if row and row["ts"] else None


def list_comments(conn, limit: int = 50) -> list[dict]:
    return _rows(conn.execute(
        """
        SELECT c.id, c.urn, c.text, c.status, c.error, c.created_at, p.title, p.company, p.url
        FROM comments c JOIN posts p ON p.urn = c.urn
        ORDER BY c.id DESC LIMIT ?
        """, (limit,)))

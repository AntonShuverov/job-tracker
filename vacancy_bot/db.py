"""SQLite queue for vacancy messages."""

import aiosqlite
from datetime import datetime, timezone

DB_PATH = "messages.db"


async def init_db(db_path: str = DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel         TEXT    NOT NULL,
                text            TEXT    NOT NULL,
                tg_link         TEXT,
                status          TEXT    NOT NULL DEFAULT 'pending',
                bot_message_id  INTEGER,
                created_at      TEXT    NOT NULL
            )
        """)
        await conn.commit()


async def add_message(db_path: str, channel: str, text: str, tg_link: str) -> int | None:
    """Insert message. Returns row id, or None if tg_link already exists (dedup)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as conn:
        if tg_link:
            cursor = await conn.execute(
                "SELECT id FROM messages WHERE tg_link = ?", (tg_link,)
            )
            if await cursor.fetchone():
                return None  # duplicate
        cursor = await conn.execute(
            "INSERT INTO messages (channel, text, tg_link, created_at) VALUES (?, ?, ?, ?)",
            (channel, text, tg_link, now),
        )
        await conn.commit()
        return cursor.lastrowid


async def get_pending(db_path: str = DB_PATH) -> list[dict]:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE status = 'pending' ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def set_sent(db_path: str, msg_id: int, bot_message_id: int) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE messages SET status = 'sent', bot_message_id = ? WHERE id = ?",
            (bot_message_id, msg_id),
        )
        await conn.commit()


async def set_saved(db_path: str, msg_id: int) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE messages SET status = 'saved' WHERE id = ?", (msg_id,)
        )
        await conn.commit()


async def set_skipped(db_path: str, msg_id: int) -> None:
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "UPDATE messages SET status = 'skipped' WHERE id = ?", (msg_id,)
        )
        await conn.commit()


async def get_message(db_path: str, msg_id: int) -> dict | None:
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM messages WHERE id = ?", (msg_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

import pytest
import aiosqlite
from db import init_db, add_message, get_pending, set_sent, set_saved, set_skipped, get_message


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


async def test_init_creates_table(db_path):
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        result = await cursor.fetchone()
    assert result is not None


async def test_add_and_get_pending(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "test_channel", "Vacancy text", "https://t.me/test/1")
    pending = await get_pending(db_path)
    assert len(pending) == 1
    assert pending[0]["id"] == msg_id
    assert pending[0]["channel"] == "test_channel"
    assert pending[0]["text"] == "Vacancy text"
    assert pending[0]["status"] == "pending"


async def test_set_sent(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "ch", "text", "link")
    await set_sent(db_path, msg_id, bot_message_id=999)
    msg = await get_message(db_path, msg_id)
    assert msg["status"] == "sent"
    assert msg["bot_message_id"] == 999


async def test_set_saved(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "ch", "text", "link")
    await set_saved(db_path, msg_id)
    msg = await get_message(db_path, msg_id)
    assert msg["status"] == "saved"


async def test_set_skipped(db_path):
    await init_db(db_path)
    msg_id = await add_message(db_path, "ch", "text", "link")
    await set_skipped(db_path, msg_id)
    msg = await get_message(db_path, msg_id)
    assert msg["status"] == "skipped"


async def test_get_pending_excludes_sent(db_path):
    await init_db(db_path)
    id1 = await add_message(db_path, "ch", "text1", "link1")
    id2 = await add_message(db_path, "ch", "text2", "link2")
    await set_sent(db_path, id2, bot_message_id=1)
    pending = await get_pending(db_path)
    assert len(pending) == 1
    assert pending[0]["id"] == id1

"""Telethon listener: monitors TG channels, writes all messages to SQLite."""

import os
import logging
from dotenv import load_dotenv
from telethon import TelegramClient, events
from db import add_message

load_dotenv()

logger = logging.getLogger("vacancy_bot")

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TG_CHANNELS = [ch.strip() for ch in os.getenv("TG_CHANNELS", "").split(",") if ch.strip()]
SESSION_FILE = "vacancy_bot_session"


def make_tg_link(username: str | None, msg_id: int) -> str:
    if not username:
        return ""
    return f"https://t.me/{str(username).lstrip('@')}/{msg_id}"


async def run_listener(db_path: str) -> None:
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.error("TELEGRAM_API_ID or TELEGRAM_API_HASH not set")
        return
    if not TG_CHANNELS:
        logger.error("TG_CHANNELS not set")
        return

    client = TelegramClient(
        SESSION_FILE, TELEGRAM_API_ID, TELEGRAM_API_HASH,
        connection_retries=10, retry_delay=5, auto_reconnect=True,
    )
    await client.start()
    me = await client.get_me()
    logger.info(f"Telethon: {me.first_name} (@{me.username})")

    channels = []
    usernames: dict[int, str | None] = {}
    for ch in TG_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            channels.append(entity)
            usernames[entity.id] = getattr(entity, "username", None)
            logger.info(f"Watching: {ch}")
        except Exception as e:
            logger.error(f"Cannot get {ch}: {e}")

    if not channels:
        logger.error("No channels resolved — check TG_CHANNELS")
        return

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        if not event.text:
            return
        chat = await event.get_chat()
        username = usernames.get(chat.id, getattr(chat, "username", None))
        tg_link = make_tg_link(username, event.id)
        channel_name = str(username or chat.id)
        try:
            result = await add_message(db_path, channel_name, event.text, tg_link)
            if result:
                logger.info(f"Queued [{channel_name}]: {event.text[:60]}...")
            else:
                logger.debug(f"Duplicate skipped [{channel_name}]")
        except Exception as e:
            logger.error(f"DB write error: {e}")

    logger.info(f"Live mode: watching {len(channels)} channel(s)")
    await client.run_until_disconnected()

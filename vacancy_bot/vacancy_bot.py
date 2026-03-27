"""Entry point: runs Telethon listener + aiogram bot concurrently."""

import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from db import init_db
from listener import run_listener
from bot import run_poller, setup_handlers

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vacancy_bot")

DB_PATH = "messages.db"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set in .env")
        return

    await init_db(DB_PATH)
    logger.info("DB ready")

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    setup_handlers(dp, DB_PATH, bot)

    logger.info("Starting vacancy bot...")
    await asyncio.gather(
        run_listener(DB_PATH),
        run_poller(DB_PATH, bot),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())

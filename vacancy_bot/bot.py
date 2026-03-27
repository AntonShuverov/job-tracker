"""aiogram bot: sends vacancy messages with Save/Skip buttons, handles responses."""

import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from db import get_pending, set_sent, set_saved, set_skipped, get_message
from processor import parse_and_save

load_dotenv()

logger = logging.getLogger("vacancy_bot")

YOUR_CHAT_ID = int(os.getenv("YOUR_CHAT_ID", "0"))
POLL_INTERVAL = 2  # seconds


class VacancyCallback(CallbackData, prefix="v"):
    action: str   # "save" or "skip"
    msg_id: int   # SQLite row id


def make_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Сохранить",
        callback_data=VacancyCallback(action="save", msg_id=msg_id),
    )
    builder.button(
        text="❌ Пропустить",
        callback_data=VacancyCallback(action="skip", msg_id=msg_id),
    )
    return builder.as_markup()


def format_message(row: dict) -> str:
    channel = row["channel"]
    tg_link = row.get("tg_link", "")
    text = row["text"]
    if len(text) > 3800:
        text = text[:3800] + "..."
    msg = f"📡 {channel}\n\n{text}"
    if tg_link:
        msg += f"\n\n🔗 {tg_link}"
    return msg


async def run_poller(db_path: str, bot: Bot) -> None:
    """Poll SQLite every POLL_INTERVAL seconds, send pending messages to user."""
    logger.info("Bot poller started")
    while True:
        try:
            pending = await get_pending(db_path)
            for row in pending:
                try:
                    sent = await bot.send_message(
                        chat_id=YOUR_CHAT_ID,
                        text=format_message(row),
                        reply_markup=make_keyboard(row["id"]),
                    )
                    await set_sent(db_path, row["id"], sent.message_id)
                    logger.info(f"Sent to bot: db_id={row['id']}")
                except Exception as e:
                    logger.error(f"Send error db_id={row['id']}: {e}")
        except Exception as e:
            logger.error(f"Poller error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


def setup_handlers(dp: Dispatcher, db_path: str, bot: Bot) -> None:
    """Register Save/Skip callback handlers."""

    @dp.callback_query(VacancyCallback.filter(F.action == "save"))
    async def handle_save(callback: CallbackQuery, callback_data: VacancyCallback):
        if callback.from_user.id != YOUR_CHAT_ID:
            await callback.answer("Нет доступа", show_alert=True)
            return
        # Must answer within 30s — do it immediately before slow Qwen/Notion calls
        await callback.answer("Сохраняю...")
        row = await get_message(db_path, callback_data.msg_id)
        if not row:
            await callback.message.edit_text("Сообщение не найдено в БД")
            return
        ok = await parse_and_save(row["text"], row["channel"], row.get("tg_link", ""))
        if ok:
            await set_saved(db_path, callback_data.msg_id)
            preview = row["text"][:200] + ("..." if len(row["text"]) > 200 else "")
            await callback.message.edit_text(f"✅ Сохранено в Notion\n\n{preview}")
        else:
            # callback.answer already called — use send_message for error notification
            # Original message retains its buttons so user can retry
            await callback.bot.send_message(
                YOUR_CHAT_ID,
                "❌ Ошибка при сохранении (Qwen/Notion). Нажми Сохранить ещё раз.",
            )

    @dp.callback_query(VacancyCallback.filter(F.action == "skip"))
    async def handle_skip(callback: CallbackQuery, callback_data: VacancyCallback):
        if callback.from_user.id != YOUR_CHAT_ID:
            await callback.answer("Нет доступа", show_alert=True)
            return
        await set_skipped(db_path, callback_data.msg_id)
        await callback.answer("Пропущено")
        await callback.message.delete()

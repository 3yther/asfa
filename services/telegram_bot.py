import os
import asyncio
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_bot_app = None
_bot_thread = None


def is_configured():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


async def _send_message_async(text: str):
    if not is_configured():
        return
    try:
        from telegram import Bot
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram send error: {e}")


def send_message(text: str):
    if not is_configured():
        return
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_send_message_async(text))
        loop.close()
    except Exception as e:
        logger.error(f"Telegram send_message error: {e}")


def start_bot():
    if not is_configured():
        logger.info("Telegram not configured — skipping bot")
        return

    def run():
        try:
            from telegram import Update
            from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

            async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
                await update.message.reply_text("ASFA online. Ask me anything.")

            async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
                if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
                    return
                user_text = update.message.text
                try:
                    from services.ai import chat
                    import database as db
                    reply = chat(user_text)
                    db.save_message("user", f"[Telegram] {user_text}")
                    db.save_message("assistant", reply)
                    await update.message.reply_text(reply[:4096])
                except Exception as e:
                    await update.message.reply_text(f"Error: {e}")

            app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(app.run_polling(allowed_updates=["message"]))
        except Exception as e:
            logger.error(f"Telegram bot error: {e}")

    global _bot_thread
    _bot_thread = threading.Thread(target=run, daemon=True, name="telegram-bot")
    _bot_thread.start()
    logger.info("Telegram bot started")

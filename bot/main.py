import logging
import os
import sys
import traceback

from fastapi import FastAPI, Request
from telegram import Update
from telegram.error import InvalidToken
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

import log_safety
from handlers import COMMAND_HANDLERS, _clears_pending, cmd_start, on_button, on_text, setup_menu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
# Safety net for everything else: strip secrets from every log line and traceback.
log_safety.install()
sys.excepthook = lambda *exc: sys.stderr.write(log_safety.redact("".join(traceback.format_exception(*exc))))
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

app = FastAPI()
tg = Application.builder().token(BOT_TOKEN).build()

tg.add_handler(CommandHandler("start", _clears_pending(cmd_start)))
for name, handler in COMMAND_HANDLERS.items():
    tg.add_handler(CommandHandler(name, handler))
tg.add_handler(CallbackQueryHandler(on_button, pattern=r"^(cmd|set|prof|goal):"))
tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))


@app.on_event("startup")
async def startup():
    try:
        await tg.initialize()
    except InvalidToken:
        # python-telegram-bot puts the full token into this error; never let it reach the logs.
        logger.error("Telegram rejected the bot token: check the latest version of secret telegram_bot_token")
        raise RuntimeError("invalid Telegram bot token") from None
    try:
        await setup_menu(tg.bot)
    except Exception as e:  # noqa: BLE001 - a menu failure must not take the bot down
        logger.error("could not register the command menu: %s", e)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg.bot)
    await tg.process_update(update)
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}

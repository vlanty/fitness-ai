import logging
import os

from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from handlers import COMMANDS, cmd_start, on_menu_button, on_text, setup_menu

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

app = FastAPI()
tg = Application.builder().token(BOT_TOKEN).build()

tg.add_handler(CommandHandler("start", cmd_start))
for name, handler in COMMANDS.items():
    tg.add_handler(CommandHandler(name, handler))
tg.add_handler(CallbackQueryHandler(on_menu_button, pattern=r"^cmd:"))
tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))


@app.on_event("startup")
async def startup():
    await tg.initialize()
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

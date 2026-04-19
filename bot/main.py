import os
import logging
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler
from handlers import cmd_start, cmd_today, cmd_week, cmd_coach, cmd_health, cmd_nutrition

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

app = FastAPI()
tg  = Application.builder().token(BOT_TOKEN).build()

tg.add_handler(CommandHandler("start",     cmd_start))
tg.add_handler(CommandHandler("today",     cmd_today))
tg.add_handler(CommandHandler("week",      cmd_week))
tg.add_handler(CommandHandler("coach",     cmd_coach))
tg.add_handler(CommandHandler("health",    cmd_health))
tg.add_handler(CommandHandler("nutrition", cmd_nutrition))

@app.on_event("startup")
async def startup():
    await tg.initialize()

@app.post("/webhook")
async def webhook(request: Request):
    data   = await request.json()
    update = Update.de_json(data, tg.bot)
    await tg.process_update(update)
    return {"ok": True}

@app.get("/health")
def health():
    return {"status": "ok"}

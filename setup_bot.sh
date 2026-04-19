#!/bin/bash
set -e

mkdir -p bot

# ── bot/Dockerfile ────────────────────────────────────────────────────────────
cat > bot/Dockerfile << 'EOF'
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
EOF

# ── bot/requirements.txt ──────────────────────────────────────────────────────
cat > bot/requirements.txt << 'EOF'
python-telegram-bot==20.8
fastapi==0.111.0
uvicorn==0.29.0
anthropic==0.26.0
google-cloud-bigquery==3.17.2
EOF

# ── bot/bq_reader.py ──────────────────────────────────────────────────────────
cat > bot/bq_reader.py << 'PYEOF'
from google.cloud import bigquery
from datetime import date, timedelta


class BigQueryReader:
    def __init__(self, project_id, dataset_id):
        self.client  = bigquery.Client(project=project_id)
        self.project = project_id
        self.dataset = dataset_id

    def _q(self, sql):
        return [dict(row) for row in self.client.query(sql).result()]

    def get_last_activity(self):
        rows = self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".activities` WHERE source = 'strava' ORDER BY activity_date DESC, start_time DESC LIMIT 1")
        return rows[0] if rows else None

    def get_week_activities(self, weeks_back=0):
        today  = date.today()
        monday = today - timedelta(days=today.weekday() + weeks_back * 7)
        sunday = monday + timedelta(days=6)
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".activities` WHERE activity_date BETWEEN '" + str(monday) + "' AND '" + str(sunday) + "' ORDER BY activity_date")

    def get_hrv_last_days(self, days=14):
        since = str(date.today() - timedelta(days=days))
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".hrv` WHERE measurement_date >= '" + since + "' ORDER BY measurement_date")

    def get_sleep_last_days(self, days=7):
        since = str(date.today() - timedelta(days=days))
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".sleep` WHERE sleep_date >= '" + since + "' ORDER BY sleep_date")

    def get_daily_stats_last_days(self, days=14):
        since = str(date.today() - timedelta(days=days))
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".daily_stats` WHERE stat_date >= '" + since + "' ORDER BY stat_date")
PYEOF

# ── bot/claude_client.py ──────────────────────────────────────────────────────
cat > bot/claude_client.py << 'PYEOF'
import anthropic

COACH_PROMPT = """Ты опытный тренер по бегу и триатлону, 15 лет работаешь со спортсменами-любителями.
Анализируй данные конкретно: что хорошо, что плохо, что изменить. Без воды и общих слов.
Спортсмен готовится к полумарафону.

Данные:
{data}

Ответ на русском, до 400 слов."""

HEALTH_PROMPT = """Ты спортивный врач. Анализируешь физиологические показатели спортсмена-любителя.

Обрати особое внимание:
- HRV тренд: снижение > 10% от нормы — флаг недовосстановления
- Пульс покоя: рост 5+ уд/мин от базового — возможная болезнь/перетренированность
- Сон: менее 7 часов или score < 60 — проблема
- Body Battery: постоянно низкий — накопленная усталость

Если видишь тревожные паттерны — говори прямо. Данные:
{data}

Ответ на русском, до 400 слов."""

NUTRITION_PROMPT = """Ты спортивный нутрициолог. Даёшь конкретные рекомендации по питанию.

Данные о тренировках и нагрузке:
{data}

Дай рекомендации с конкретными цифрами:
- Калорийность в тренировочные и восстановительные дни
- Питание за 2-3 часа до тренировки и в течение 30 минут после
- Гидратация (мл/час при данном типе нагрузки)
- Если есть длинные тренировки — стратегия питания на дистанции

Ответ на русском, до 400 слов."""


def analyze(prompt_template, data_summary, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt_template.format(data=data_summary)}],
    )
    return msg.content[0].text
PYEOF

# ── bot/handlers.py ───────────────────────────────────────────────────────────
cat > bot/handlers.py << 'PYEOF'
import os
import logging
from telegram import Update
from telegram.ext import ContextTypes
from bq_reader import BigQueryReader
from claude_client import analyze, COACH_PROMPT, HEALTH_PROMPT, NUTRITION_PROMPT

logger = logging.getLogger(__name__)

def _bq():
    return BigQueryReader(os.environ["GCP_PROJECT"], os.environ["BQ_DATASET"])

def _fmt_activity(a):
    dist     = (a.get("distance_meters") or 0) / 1000
    dur      = (a.get("duration_seconds") or 0) // 60
    pace     = a.get("avg_pace_sec_per_km")
    pace_str = (str(int(pace // 60)) + ":" + str(int(pace % 60)).zfill(2) + "/км") if pace else "—"
    return ("*" + str(a.get("name", "—")) + "* (" + str(a.get("type", "—")) + ")\n"
            + str(a.get("activity_date")) + " | " + str(round(dist, 1)) + " км | " + str(dur) + " мин\n"
            + "Темп: " + pace_str + " | ЧСС avg: " + str(a.get("avg_heart_rate") or "—") + "\n"
            + "Набор: " + str(round(a.get("elevation_gain") or 0)) + " м | Калории: " + str(a.get("calories") or "—"))

def _fmt_sleep(s):
    total = (s.get("duration_seconds") or 0) // 3600
    deep  = (s.get("deep_sleep_seconds") or 0) // 60
    rem   = (s.get("rem_sleep_seconds") or 0) // 60
    return "😴 " + str(s["sleep_date"]) + ": " + str(total) + "ч | Deep " + str(deep) + "мин | REM " + str(rem) + "мин | Score " + str(s.get("sleep_score") or "—")

def _fmt_hrv(h):
    return "💓 " + str(h["measurement_date"]) + ": HRV " + str(h.get("last_night_avg") or "—") + " (7д avg: " + str(h.get("weekly_avg") or "—") + ") — " + str(h.get("status") or "—")

def _fmt_stats(s):
    return ("📊 " + str(s["stat_date"]) + ": ЧСС покоя " + str(s.get("resting_heart_rate") or "—")
            + " | BB " + str(s.get("body_battery_low") or "—") + "→" + str(s.get("body_battery_high") or "—")
            + " | Шаги " + str(s.get("total_steps") or "—"))


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏃 Fitness AI\n\n"
        "/today — последняя тренировка\n"
        "/week — тренировки этой недели\n"
        "/coach — анализ тренера\n"
        "/health — анализ спортврача (HRV, сон, усталость)\n"
        "/nutrition — рекомендации нутрициолога"
    )


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bq    = _bq()
    act   = bq.get_last_activity()
    hrv   = bq.get_hrv_last_days(1)
    sleep = bq.get_sleep_last_days(1)
    stats = bq.get_daily_stats_last_days(1)
    parts = ["*Последняя тренировка*\n"]
    parts.append(_fmt_activity(act) if act else "Активностей не найдено")
    if hrv:   parts.append("\n" + _fmt_hrv(hrv[-1]))
    if sleep: parts.append(_fmt_sleep(sleep[-1]))
    if stats: parts.append(_fmt_stats(stats[-1]))
    await update.message.reply_text("\n".join(parts), parse_mode="Markdown")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    bq   = _bq()
    acts = bq.get_week_activities()
    if not acts:
        await update.message.reply_text("За эту неделю нет активностей")
        return
    total_dist = sum((a.get("distance_meters") or 0) for a in acts) / 1000
    total_dur  = sum((a.get("duration_seconds") or 0) for a in acts) // 60
    parts = ["*Неделя: " + str(len(acts)) + " тренировок | " + str(round(total_dist, 1)) + " км | " + str(total_dur) + " мин*\n"]
    for a in acts:
        parts.append(_fmt_activity(a))
    await update.message.reply_text("\n".join(parts), parse_mode="Markdown")


async def _ai_handler(update: Update, prompt_template: str, label: str):
    bq    = _bq()
    acts  = bq.get_week_activities()
    hrv   = bq.get_hrv_last_days(14)
    sleep = bq.get_sleep_last_days(7)
    stats = bq.get_daily_stats_last_days(14)

    data_summary = (
        "=== АКТИВНОСТИ (текущая неделя) ===\n" +
        ("\n".join(_fmt_activity(a) for a in acts) or "нет данных") + "\n\n" +
        "=== HRV (14 дней) ===\n" +
        ("\n".join(_fmt_hrv(h) for h in hrv) or "нет данных") + "\n\n" +
        "=== СОН (7 дней) ===\n" +
        ("\n".join(_fmt_sleep(s) for s in sleep) or "нет данных") + "\n\n" +
        "=== ЕЖЕДНЕВНАЯ СТАТИСТИКА (14 дней) ===\n" +
        ("\n".join(_fmt_stats(s) for s in stats) or "нет данных")
    )

    await update.message.reply_text("Анализирую (" + label + ")...")
    try:
        result = analyze(prompt_template, data_summary, os.environ["ANTHROPIC_API_KEY"])
        await update.message.reply_text(result)
    except Exception as e:
        logger.error("Claude error: " + str(e))
        await update.message.reply_text("Ошибка анализа: " + str(e))


async def cmd_coach(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _ai_handler(update, COACH_PROMPT, "тренер")

async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _ai_handler(update, HEALTH_PROMPT, "спортврач")

async def cmd_nutrition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _ai_handler(update, NUTRITION_PROMPT, "нутрициолог")
PYEOF

# ── bot/main.py ───────────────────────────────────────────────────────────────
cat > bot/main.py << 'PYEOF'
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
PYEOF

echo "✅ bot/ создан:"
ls -1 bot/

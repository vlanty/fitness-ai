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

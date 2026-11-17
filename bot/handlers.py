import logging
import os

from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from bq_reader import BigQueryReader, stale_warnings
from claude_client import ASK_PROMPT, COACH_PROMPT, HEALTH_PROMPT, NUTRITION_PROMPT, analyze

logger = logging.getLogger(__name__)

SOURCE_LABEL = {"garmin": "Garmin", "strava": "Strava"}


def _bq():
    return BigQueryReader(os.environ["GCP_PROJECT"], os.environ["BQ_DATASET"])


def _enabled_sources():
    return [s.strip() for s in os.environ.get("DATA_SOURCES", "").split(",") if s.strip()]


def _allowed(update: Update) -> bool:
    """Only the owner's chat may use the bot (it exposes health data and spends Claude credits)."""
    owner = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    chat = update.effective_chat
    return bool(owner) and chat is not None and str(chat.id) == owner


def _md(v) -> str:
    return escape_markdown(str(v), version=1)


def _hm(seconds) -> str:
    if not seconds:
        return "—"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}ч {m:02d}м"


def _fmt_activity(a):
    dist = (a.get("distance_meters") or 0) / 1000
    dur = (a.get("duration_seconds") or 0) // 60
    pace = a.get("avg_pace_sec_per_km")
    pace_str = f"{int(pace // 60)}:{int(pace % 60):02d}/км" if pace else "—"
    src = SOURCE_LABEL.get(a.get("source"), a.get("source") or "—")
    lines = [
        f"*{_md(a.get('name') or '—')}* ({_md(a.get('type') or '—')}, {src})",
        f"{a.get('activity_date')} | {round(dist, 1)} км | {dur} мин",
        f"Темп: {pace_str} | ЧСС avg: {a.get('avg_heart_rate') or '—'} | max: {a.get('max_heart_rate') or '—'}",
        f"Набор: {round(a.get('elevation_gain') or 0)} м | Калории: {a.get('calories') or '—'}",
    ]
    extra = []
    if a.get("aerobic_te") is not None:
        extra.append(f"TE аэроб {a['aerobic_te']:.1f} / анаэроб {a.get('anaerobic_te') or 0:.1f}")
    if a.get("training_load") is not None:
        extra.append(f"нагрузка {round(a['training_load'])}")
    if a.get("vo2max") is not None:
        extra.append(f"VO2max {a['vo2max']:.0f}")
    if extra:
        lines.append(" | ".join(extra))
    return "\n".join(lines)


def _fmt_health(h):
    parts = [f"📅 {h['date']}"]
    if h.get("sleep_seconds"):
        parts.append(f"😴 сон {_hm(h['sleep_seconds'])} (deep {_hm(h.get('deep_sleep_seconds'))}, "
                     f"REM {_hm(h.get('rem_sleep_seconds'))}, score {h.get('sleep_score') or '—'})")
    if h.get("hrv_last_night"):
        parts.append(f"💓 HRV {h['hrv_last_night']} (7д {h.get('hrv_weekly_avg') or '—'}, {h.get('hrv_status') or '—'})")
    vitals = []
    if h.get("resting_hr"):
        vitals.append(f"пульс покоя {h['resting_hr']}")
    if h.get("stress_avg") is not None:
        vitals.append(f"стресс {h['stress_avg']}")
    if h.get("body_battery_high") is not None:
        vitals.append(f"BB {h.get('body_battery_low') or '—'}→{h['body_battery_high']}")
    if h.get("steps"):
        vitals.append(f"шаги {h['steps']}")
    if vitals:
        parts.append("📊 " + " | ".join(vitals))
    return "\n".join(parts)


def _context(bq: BigQueryReader) -> str:
    """Data block for Claude: what we have, plus explicit gaps."""
    last = bq.get_last_activity()
    acts = bq.get_week_activities()
    prev = bq.get_week_activities(weeks_back=1)
    health = bq.get_health_last_days(14)
    warn = stale_warnings(bq.get_source_status(), _enabled_sources())
    return (
        "=== ПОСЛЕДНЯЯ ТРЕНИРОВКА ===\n" + (_fmt_activity(last) if last else "нет данных") + "\n\n"
        "=== ТРЕНИРОВКИ: ТЕКУЩАЯ НЕДЕЛЯ ===\n" + ("\n\n".join(_fmt_activity(a) for a in acts) or "нет") + "\n\n"
        "=== ТРЕНИРОВКИ: ПРОШЛАЯ НЕДЕЛЯ ===\n" + ("\n\n".join(_fmt_activity(a) for a in prev) or "нет") + "\n\n"
        "=== ЗДОРОВЬЕ ПО ДНЯМ (14 дней: сон, HRV, пульс покоя, стресс, Body Battery) ===\n"
        + ("\n".join(_fmt_health(h) for h in health) or "нет данных") + "\n\n"
        + ("=== СВЕЖЕСТЬ ДАННЫХ ===\n" + "\n".join(warn) if warn else "")
    )


async def _guard(update: Update) -> bool:
    if _allowed(update):
        return True
    logger.warning("отклонён чат %s", update.effective_chat.id if update.effective_chat else "?")
    return False


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    await update.message.reply_text(
        "🏃 Fitness AI\n\n"
        "/today — последняя тренировка и вчерашний день\n"
        "/week — тренировки этой недели\n"
        "/coach — анализ тренера\n"
        "/health — анализ спортврача (HRV, сон, усталость)\n"
        "/nutrition — рекомендации нутрициолога\n"
        "/status — свежесть данных по источникам\n\n"
        "Или просто спросите: «как прошла последняя тренировка?», «как я спал вчера?»"
    )


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    bq = _bq()
    act = bq.get_last_activity()
    health = bq.get_health_last_days(2)
    parts = ["*Последняя тренировка*", _fmt_activity(act) if act else "Активностей не найдено"]
    if health:
        parts += ["", "*Здоровье*", _fmt_health(health[-1])]
    parts += stale_warnings(bq.get_source_status(), _enabled_sources())
    await update.message.reply_text("\n".join(parts), parse_mode="Markdown")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    acts = _bq().get_week_activities()
    if not acts:
        await update.message.reply_text("За эту неделю нет активностей")
        return
    total_dist = sum((a.get("distance_meters") or 0) for a in acts) / 1000
    total_dur = sum((a.get("duration_seconds") or 0) for a in acts) // 60
    parts = [f"*Неделя: {len(acts)} тренировок | {round(total_dist, 1)} км | {total_dur} мин*\n"]
    parts += [_fmt_activity(a) for a in acts]
    await update.message.reply_text("\n\n".join(parts), parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    rows = _bq().get_source_status()
    enabled = _enabled_sources()
    lines = [f"Источники: {', '.join(enabled) or '—'}"]
    for r in rows:
        mark = "✅" if r.get("last_status") == "ok" else "❌"
        when = f"{r['last_success_at']:%d.%m %H:%M} UTC" if r.get("last_success_at") else "никогда"
        lines.append(f"{mark} {r['source']}: успешно {when}, данные по {r.get('cursor_date') or '—'}")
    lines += stale_warnings(rows, enabled)
    await update.message.reply_text("\n".join(lines))


async def _ai_reply(update: Update, prompt: str, label: str, **fmt):
    await update.message.reply_text(f"Анализирую ({label})...")
    try:
        result = analyze(prompt, _context(_bq()), os.environ["ANTHROPIC_API_KEY"], **fmt)
        await update.message.reply_text(result)
    except Exception as e:  # noqa: BLE001
        logger.error("Claude error: %s", e)
        await update.message.reply_text("Ошибка анализа, подробности в логах.")


async def cmd_coach(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _guard(update):
        await _ai_reply(update, COACH_PROMPT, "тренер")


async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _guard(update):
        await _ai_reply(update, HEALTH_PROMPT, "спортврач")


async def cmd_nutrition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _guard(update):
        await _ai_reply(update, NUTRITION_PROMPT, "нутрициолог")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Free-form question, e.g. «как прошла последняя тренировка?», «как я спал вчера?»."""
    if await _guard(update):
        await _ai_reply(update, ASK_PROMPT, "смотрю данные", question=update.message.text[:1000])

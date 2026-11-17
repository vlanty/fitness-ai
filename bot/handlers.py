import logging
import os

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from bq_reader import BigQueryReader, stale_warnings
from claude_client import ASK_PROMPT, COACH_PROMPT, HEALTH_PROMPT, NUTRITION_PROMPT, Refused, analyze
from estimates import readiness, training_status
from i18n import SUPPORTED, resolve, t

logger = logging.getLogger(__name__)

SOURCE_LABEL = {"garmin": "Garmin", "strava": "Strava"}
CONTEXT_LANG = "en"  # data handed to Claude is formatted in English; Claude answers in the user's language

# Commands in the Telegram "Menu" list, in display order. /start stays hidden (Telegram sends it itself).
MENU_COMMANDS = ["menu", "today", "week", "coach", "health", "nutrition", "status"]
# Inline buttons under /start and /menu, two per row.
MENU_BUTTONS = [["today", "week"], ["coach", "health"], ["nutrition", "status"]]


def _bq():
    return BigQueryReader(os.environ["GCP_PROJECT"], os.environ["BQ_DATASET"])


def _enabled_sources():
    return [s.strip() for s in os.environ.get("DATA_SOURCES", "").split(",") if s.strip()]


def _owner() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def _allowed(update: Update) -> bool:
    """Only the owner's chat may use the bot (it exposes health data and spends Claude credits)."""
    owner = _owner()
    chat = update.effective_chat
    return bool(owner) and chat is not None and str(chat.id) == owner


def _lang(update: Update) -> str:
    user = update.effective_user
    return resolve(user.language_code if user else None)


def _md(v) -> str:
    return escape_markdown(str(v), version=1)


def _hm(seconds, lang) -> str:
    if not seconds:
        return "—"
    h, m = divmod(int(seconds) // 60, 60)
    return t(lang, "fmt.hm", h=h, m=m)


def _fmt_activity(a, lang):
    dist = (a.get("distance_meters") or 0) / 1000
    dur = (a.get("duration_seconds") or 0) // 60
    pace = a.get("avg_pace_sec_per_km")
    pace_str = t(lang, "act.pace", m=int(pace // 60), s=int(pace % 60)) if pace else "—"
    src = SOURCE_LABEL.get(a.get("source"), a.get("source") or "—")
    lines = [
        f"*{_md(a.get('name') or '—')}* ({_md(a.get('type') or '—')}, {src})",
        t(lang, "act.line2", date=a.get("activity_date"), km=round(dist, 1), min=dur),
        t(lang, "act.line3", pace=pace_str, avg=a.get("avg_heart_rate") or "—", max=a.get("max_heart_rate") or "—"),
        t(lang, "act.line4", elev=round(a.get("elevation_gain") or 0), cal=a.get("calories") or "—"),
    ]
    extra = []
    if a.get("aerobic_te") is not None:
        extra.append(t(lang, "act.te", a=f"{a['aerobic_te']:.1f}", an=f"{a.get('anaerobic_te') or 0:.1f}"))
    if a.get("training_load") is not None:
        extra.append(t(lang, "act.load", v=round(a["training_load"])))
    if a.get("vo2max") is not None:
        extra.append(f"VO2max {a['vo2max']:.0f}")
    if extra:
        lines.append(" | ".join(extra))
    return "\n".join(lines)


def _fmt_health(h, lang):
    parts = [f"📅 {h['date']}"]
    if h.get("sleep_seconds"):
        parts.append(t(lang, "health.sleep", total=_hm(h["sleep_seconds"], lang),
                       deep=_hm(h.get("deep_sleep_seconds"), lang), rem=_hm(h.get("rem_sleep_seconds"), lang),
                       score=h.get("sleep_score") or "—"))
    if h.get("hrv_last_night"):
        norm = (t(lang, "health.hrv_norm", lo=h["hrv_baseline_low"], hi=h["hrv_baseline_high"])
                if h.get("hrv_baseline_low") and h.get("hrv_baseline_high") else "")
        parts.append(t(lang, "health.hrv", v=h["hrv_last_night"], w=h.get("hrv_weekly_avg") or "—",
                       norm=norm, status=h.get("hrv_status") or "—"))
    vitals = []
    if h.get("resting_hr"):
        vitals.append(t(lang, "health.rhr", v=h["resting_hr"]))
    if h.get("stress_avg") is not None:
        vitals.append(t(lang, "health.stress", v=h["stress_avg"]))
    if h.get("body_battery_high") is not None:
        vitals.append(f"BB {h.get('body_battery_low') or '—'}→{h['body_battery_high']}")
    if h.get("steps"):
        vitals.append(t(lang, "health.steps", v=h["steps"]))
    if vitals:
        parts.append("📊 " + " | ".join(vitals))
    return "\n".join(parts)


FOCUS = {"focus_low_aerobic_28d": "low_aerobic", "focus_high_aerobic_28d": "high_aerobic",
         "focus_anaerobic_28d": "anaerobic"}


def _fmt_load(l, lang):
    if not l:
        return t(lang, "load.none")
    ratio = l.get("load_ratio")
    ratio_str = (t(lang, "load.ratio", r=f"{ratio:.2f}", zone=l["load_ratio_zone"])
                 if ratio is not None and l.get("load_ratio_zone") else t(lang, "load.short_history"))
    lines = [t(lang, "load.line", acute=round(l.get("acute_load") or 0),
               chronic=round(l.get("chronic_load") or 0), ratio=ratio_str)]
    total = sum(l.get(k) or 0 for k in FOCUS)
    if total:
        lines.append(t(lang, "load.focus", parts=", ".join(
            f"{t(lang, 'load.focus.' + name)} {round(100 * (l.get(k) or 0) / total)}%" for k, name in FOCUS.items())))
    if l.get("vo2max") is not None:
        old = l.get("vo2max_28d_ago")
        lines.append(t(lang, "load.vo2", v=f"{l['vo2max']:.1f}")
                     + (t(lang, "load.vo2_trend", d=f"{l['vo2max'] - old:+.1f}") if old is not None else ""))
    return "\n".join(lines)


def _estimates(health, load, lang) -> str:
    return (readiness(health, load, lang=lang).text(t(lang, "est.readiness")) + "\n"
            + training_status(load, health, lang=lang).text(t(lang, "est.status")))


def _context(bq: BigQueryReader) -> str:
    """Data block for Claude: what we have, plus explicit gaps. Always in English."""
    lang = CONTEXT_LANG
    last = bq.get_last_activity()
    acts = bq.get_week_activities()
    prev = bq.get_week_activities(weeks_back=1)
    health = bq.get_health_last_days(14)
    loads = bq.get_load_last_days(1)
    load = loads[-1] if loads else None
    warn = stale_warnings(bq.get_source_status(), _enabled_sources(), lang)
    return (
        "=== LAST WORKOUT ===\n" + (_fmt_activity(last, lang) if last else "no data") + "\n\n"
        "=== WORKOUTS: THIS WEEK ===\n" + ("\n\n".join(_fmt_activity(a, lang) for a in acts) or "none") + "\n\n"
        "=== WORKOUTS: LAST WEEK ===\n" + ("\n\n".join(_fmt_activity(a, lang) for a in prev) or "none") + "\n\n"
        "=== HEALTH BY DAY (14 days: sleep, HRV, resting HR, stress, Body Battery) ===\n"
        + ("\n".join(_fmt_health(h, lang) for h in health) or "no data") + "\n\n"
        "=== TRAINING LOAD (computed by fitness-ai from Garmin data) ===\n" + _fmt_load(load, lang) + "\n\n"
        "=== fitness-ai ESTIMATES (NOT Garmin metrics; call them our estimate) ===\n"
        + _estimates(health, load, lang) + "\n\n"
        + ("=== DATA FRESHNESS ===\n" + "\n".join(warn) if warn else "")
    )


async def _guard(update: Update) -> bool:
    if _allowed(update):
        return True
    logger.warning("rejected chat %s", update.effective_chat.id if update.effective_chat else "?")
    return False


async def _reply(update: Update, text: str, **kw):
    await update.effective_message.reply_text(text, **kw)


# ---------- menu ----------

def _menu_markup(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, f"btn.{c}"), callback_data=f"cmd:{c}") for c in row]
                                 for row in MENU_BUTTONS])


async def setup_menu(bot) -> None:
    """Register the command list shown under Telegram's "Menu" button, only in the owner's chat."""
    owner = _owner()
    if not owner:
        logger.warning("TELEGRAM_CHAT_ID is not set; command menu not registered")
        return
    scope = BotCommandScopeChat(int(owner))
    for lang in SUPPORTED:
        commands = [BotCommand(c, t(lang, f"cmd.{c}")) for c in MENU_COMMANDS]
        # English is also the fallback for any other Telegram language.
        await bot.set_my_commands(commands, scope=scope, language_code=None if lang == "en" else lang)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    await _reply(update, t(lang, "menu.title"), reply_markup=_menu_markup(lang))


cmd_menu = cmd_start


async def on_menu_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return
    handler = COMMANDS.get((query.data or "").removeprefix("cmd:"))
    if handler:
        await handler(update, ctx)


# ---------- data commands ----------

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    bq = _bq()
    act = bq.get_last_activity()
    health = bq.get_health_last_days(4)
    loads = bq.get_load_last_days(1)
    load = loads[-1] if loads else None
    parts = [f"*{t(lang, 'today.last_workout')}*",
             _fmt_activity(act, lang) if act else t(lang, "today.no_activities")]
    if health:
        parts += ["", f"*{t(lang, 'today.health')}*", _fmt_health(health[-1], lang)]
    if load:
        parts += ["", f"*{t(lang, 'today.load')}*", _md(_fmt_load(load, lang))]
    parts += ["", _md(_estimates(health, load, lang))]
    parts += stale_warnings(bq.get_source_status(), _enabled_sources(), lang)
    await _reply(update, "\n".join(parts), parse_mode="Markdown")


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    acts = _bq().get_week_activities()
    if not acts:
        await _reply(update, t(lang, "week.empty"))
        return
    total_dist = sum((a.get("distance_meters") or 0) for a in acts) / 1000
    total_dur = sum((a.get("duration_seconds") or 0) for a in acts) // 60
    parts = ["*" + t(lang, "week.header", n=len(acts), km=round(total_dist, 1), min=total_dur) + "*\n"]
    parts += [_fmt_activity(a, lang) for a in acts]
    await _reply(update, "\n\n".join(parts), parse_mode="Markdown")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    rows = _bq().get_source_status()
    enabled = _enabled_sources()
    lines = [t(lang, "status.sources", list=", ".join(enabled) or "—")]
    for r in rows:
        mark = "✅" if r.get("last_status") == "ok" else "❌"
        when = f"{r['last_success_at']:%d.%m %H:%M} UTC" if r.get("last_success_at") else t(lang, "status.never")
        lines.append(t(lang, "status.line", mark=mark, source=r["source"], when=when,
                       cursor=r.get("cursor_date") or "—"))
    lines += stale_warnings(rows, enabled, lang)
    await _reply(update, "\n".join(lines))


# ---------- AI ----------

async def _ai_reply(update: Update, prompt: str, label: str, task: str, **fmt):
    lang = _lang(update)
    await _reply(update, t(lang, "ai.working", label=t(lang, f"ai.label.{label}")))
    try:
        result = analyze(prompt, _context(_bq()), task, lang, **fmt)
        await _reply(update, result or t(lang, "ai.error"))
    except Refused as e:
        logger.warning("Claude refused: %s", e)
        await _reply(update, t(lang, "ai.refused"))
    except Exception as e:  # noqa: BLE001
        logger.error("Claude error: %s", e)
        await _reply(update, t(lang, "ai.error"))


async def cmd_coach(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _guard(update):
        await _ai_reply(update, COACH_PROMPT, "coach", "analysis")


async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _guard(update):
        await _ai_reply(update, HEALTH_PROMPT, "health", "analysis")


async def cmd_nutrition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if await _guard(update):
        await _ai_reply(update, NUTRITION_PROMPT, "nutrition", "analysis")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Free-form question, e.g. "how did my last workout go?"."""
    if await _guard(update):
        await _ai_reply(update, ASK_PROMPT, "ask", "chat", question=update.message.text[:1000])


COMMANDS = {
    "menu": cmd_menu, "today": cmd_today, "week": cmd_week, "coach": cmd_coach,
    "health": cmd_health, "nutrition": cmd_nutrition, "status": cmd_status,
}

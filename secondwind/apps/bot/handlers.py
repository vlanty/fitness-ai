import logging
import os
from datetime import date, timedelta
from functools import lru_cache

from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from secondwind.adapters.llm.claude import ClaudeLLM
from secondwind.adapters.warehouse.bigquery_reader import BigQueryReader
from secondwind.core.assistant import ASK_PROMPT, COACH_PROMPT, HEALTH_PROMPT, NUTRITION_PROMPT, Refused, analyze
from secondwind.core.freshness import stale_warnings
from secondwind.core.router import Route, route as route_message
from secondwind.core.ports import LLM, DataReader, StateStore
from secondwind.core import analytics
from secondwind.apps.bot import charts, tenancy
from secondwind.core.athlete import DISTANCES, PROFILE_FIELDS, athlete_note, compute_zones
from secondwind.core.estimates import readiness, training_status
from secondwind.core.i18n import SUPPORTED, resolve, t
from secondwind.core.models import DEFAULT_MODE
from secondwind.adapters.state.firestore import FirestoreStore
from secondwind.apps.bot.user_ui import (apply_setting, ask_text, distance_keyboard, handle_input, render_goal, render_profile,
                     render_settings)
from secondwind.apps.bot.tg_format import html_to_plain, md_to_html, split_message
from secondwind.apps.bot.user_ui import _zones_title

logger = logging.getLogger(__name__)

SOURCE_LABEL = {"garmin": "Garmin", "strava": "Strava"}
CONTEXT_LANG = "en"  # data handed to Claude is formatted in English; Claude answers in the user's language

# Commands in the Telegram "Menu" list, in display order. /start stays hidden (Telegram sends it itself).
MENU_COMMANDS = ["menu", "today", "week", "month", "compare", "records", "charts", "coach", "health", "nutrition",
                 "goal", "profile", "settings", "status"]
# Inline buttons under /start and /menu, two per row.
MENU_BUTTONS = [["today", "week"], ["month", "compare"], ["records", "charts"], ["coach", "health"],
                ["nutrition", "goal"], ["profile", "settings"], ["status"]]


def _bq(update: Update) -> DataReader:
    """This chat's user's data."""
    return BigQueryReader(os.environ["GCP_PROJECT"], os.environ["BQ_DATASET"], _uid(update))


def _enabled_sources():
    return [s.strip() for s in os.environ.get("DATA_SOURCES", "").split(",") if s.strip()]


def _owner() -> str:
    return tenancy.owner_chat()


def _allowed(update: Update) -> bool:
    """Only chats that map to a user may use the bot (it exposes health data and spends AI credits).
    In solo mode that is the owner's chat, and only once OWNER_USER_ID is set."""
    chat = update.effective_chat
    return tenancy.user_for_chat(chat.id if chat is not None else None) is not None


@lru_cache(maxsize=1)
def _store() -> StateStore:
    return FirestoreStore(os.environ["GCP_PROJECT"])


@lru_cache(maxsize=1)
def _llm() -> LLM:
    return ClaudeLLM()


def _uid(update: Update) -> str:
    """The internal user_id of this chat (callers have passed _guard)."""
    return tenancy.user_for_chat(update.effective_chat.id)


def _user(update: Update) -> dict:
    """The user's stored state; {} if storage is unavailable (the bot keeps working without it)."""
    uid = _uid(update) if update.effective_chat is not None else None
    if uid is None:
        return {}
    try:
        return _store().get_user(uid)
    except Exception as e:  # noqa: BLE001
        logger.error("state read failed: %s", e)
        return {}


def _lang(update: Update, user: dict | None = None) -> str:
    """Language chosen in /settings, else BOT_LANGUAGE (set at deploy), else the Telegram app language."""
    chosen = ((user if user is not None else _user(update)).get("settings") or {}).get("lang")
    if chosen in SUPPORTED:
        return chosen
    forced = os.environ.get("BOT_LANGUAGE", "").strip()
    if forced:
        return resolve(forced)
    tg_user = update.effective_user
    return resolve(tg_user.language_code if tg_user else None)


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


def _context(bq: DataReader) -> str:
    """Data block for Claude: what we have, plus explicit gaps. Always in English."""
    lang = CONTEXT_LANG
    last = bq.get_last_activity()
    acts = bq.get_week_activities()
    prev = bq.get_week_activities(weeks_back=1)
    health = bq.get_health_last_days(14)
    loads = bq.get_load_last_days(1)
    load = loads[-1] if loads else None
    warn = stale_warnings(bq.get_source_status(), _enabled_sources(), lang)
    today = date.today()
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    try:
        month = analytics.month_note(bq.get_period(month_start, today), bq.get_period(prev_end.replace(day=1), prev_end))
        records = analytics.records_note(bq.get_records(today - timedelta(days=365)))
    except Exception as e:  # noqa: BLE001 - extra context must not block the answer
        logger.error("analytics context failed: %s", e)
        month = records = "unavailable"
    return (
        "=== LAST WORKOUT ===\n" + (_fmt_activity(last, lang) if last else "no data") + "\n\n"
        "=== WORKOUTS: THIS WEEK ===\n" + ("\n\n".join(_fmt_activity(a, lang) for a in acts) or "none") + "\n\n"
        "=== WORKOUTS: LAST WEEK ===\n" + ("\n\n".join(_fmt_activity(a, lang) for a in prev) or "none") + "\n\n"
        "=== HEALTH BY DAY (14 days: sleep, HRV, resting HR, stress, Body Battery) ===\n"
        + ("\n".join(_fmt_health(h, lang) for h in health) or "no data") + "\n\n"
        "=== TRAINING LOAD (computed by Second Wind from Garmin data) ===\n" + _fmt_load(load, lang) + "\n\n"
        "=== SECOND WIND ESTIMATES (NOT Garmin metrics; call them our estimate) ===\n"
        + _estimates(health, load, lang) + "\n\n"
        "=== MONTH ===\n" + month + "\n\n"
        "=== PERSONAL RECORDS (best efforts within runs, Garmin) ===\n" + records + "\n\n"
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
    # A language fixed in /settings or by BOT_LANGUAGE applies whatever the Telegram app language is.
    try:
        uid = tenancy.owner_user_id()
        chosen = (_store().get_user(uid).get("settings") or {}).get("lang") if uid else None
    except Exception as e:  # noqa: BLE001
        logger.error("state read failed: %s", e)
        chosen = None
    forced = chosen if chosen in SUPPORTED else (resolve(os.environ["BOT_LANGUAGE"])
                                                 if os.environ.get("BOT_LANGUAGE", "").strip() else None)
    for lang in SUPPORTED:
        shown = forced or lang
        commands = [BotCommand(c, t(shown, f"cmd.{c}")) for c in MENU_COMMANDS]
        # The English list (no language_code) is also the fallback for any other Telegram language.
        await bot.set_my_commands(commands, scope=scope, language_code=None if lang == "en" else lang)
    await _set_names(bot)


async def _set_names(bot) -> None:
    """Bot display name per Telegram language (e.g. "Вита · Второе дыхание"); English is the fallback.
    Only changed when different: setMyName is strictly rate-limited."""
    for lang in SUPPORTED:
        code = None if lang == "en" else lang
        name = t(lang, "bot.name")
        try:
            if (await bot.get_my_name(language_code=code)).name != name:
                await bot.set_my_name(name, language_code=code)
        except Exception as e:  # noqa: BLE001 - a name is cosmetic; never break startup over it
            logger.warning("bot name for %s not set: %s", lang, e)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    await _reply(update, t(lang, "menu.title"), reply_markup=_menu_markup(lang))


cmd_menu = cmd_start


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """All inline buttons: cmd:<command>, set:<key>:<value>, prof:..., goal:..."""
    query = update.callback_query
    await query.answer()
    if not await _guard(update):
        return
    kind, _, rest = (query.data or "").partition(":")
    if kind == "cmd" and rest in COMMANDS:
        await COMMANDS[rest](update, ctx)
    elif kind == "set":
        await _on_setting(update, *rest.partition(":")[::2])
    elif kind == "prof":
        await _on_profile_button(update, rest)
    elif kind == "goal":
        await _on_goal_button(update, rest)
    elif kind == "cmp" and rest in analytics.COMPARE_SPANS:
        await _send_compare(update, rest)
    elif kind == "chart" and rest in charts.CHARTS:
        await _send_chart(update, rest)


# ---------- analytics without AI ----------

async def cmd_records(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    rows = _bq(update).get_records(date.today() - timedelta(days=365))
    await _reply(update, analytics.render_records(lang, rows), parse_mode="HTML")


async def cmd_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    today = date.today()
    prev_end = today.replace(day=1) - timedelta(days=1)
    bq = _bq(update)
    text = analytics.render_month(lang, today, bq.get_period(today.replace(day=1), today),
                                  bq.get_period(prev_end.replace(day=1), prev_end))
    await _reply(update, text, parse_mode="HTML")


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, f"cmp.btn.{s}"), callback_data=f"cmp:{s}")
                                for s in analytics.COMPARE_SPANS]])
    await _reply(update, t(lang, "cmp.pick"), reply_markup=kb)


async def _send_compare(update: Update, span: str):
    lang = _lang(update)
    today = date.today()
    (a0, a1), (b0, b1) = analytics.periods(span, today)
    bq = _bq(update)
    text = analytics.render_compare(lang, span, today, bq.get_period(a0, a1), bq.get_period(b0, b1))
    await _reply(update, text, parse_mode="HTML")


async def cmd_charts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    names = list(charts.CHARTS)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, f"chart.btn.{c}"), callback_data=f"chart:{c}")
                                for c in names[i:i + 2]] for i in range(0, len(names), 2)])
    await _reply(update, t(lang, "chart.pick"), reply_markup=kb)


async def _send_chart(update: Update, name: str):
    lang = _lang(update)
    png = charts.render(name, lang, _bq(update))
    await update.effective_message.reply_photo(InputFile(png, filename=f"{name}.png"))


# ---------- settings, profile, goal ----------

async def _show(update: Update, text: str, kb, edit: bool = False):
    """Show a screen: edit the message with the pressed button, or send a new one."""
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
            return
        except BadRequest as e:  # e.g. "message is not modified"
            logger.info("edit skipped: %s", e)
            return
    await _reply(update, text, parse_mode="HTML", reply_markup=kb)


async def _stored(update: Update, lang: str, action) -> bool:
    """Run a state write; tell the user if storage is down."""
    try:
        action()
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("state write failed: %s", e)
        await _reply(update, t(lang, "storage.error"))
        return False


def _athlete_inputs(update: Update) -> dict:
    """Watch zones, threshold trend and Garmin resting HR; {} if BigQuery can't answer."""
    try:
        return _bq(update).get_athlete_inputs()
    except Exception as e:  # noqa: BLE001
        logger.error("athlete inputs read failed: %s", e)
        return {}


def _month() -> str:
    return date.today().strftime("%Y-%m")


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    if not await _guard(update):
        return
    user = _user(update)
    lang = _lang(update, user)
    try:
        usage = _store().get_usage(_uid(update), _month())
    except Exception as e:  # noqa: BLE001
        logger.error("usage read failed: %s", e)
        usage = {}
    await _show(update, *render_settings(lang, user.get("settings") or {}, usage), edit=edit)


async def _on_setting(update: Update, key: str, value: str):
    lang = _lang(update)
    ok = await _stored(update, lang, lambda: apply_setting(_store(), _uid(update), key, value))
    if ok:
        if key == "lang":
            await setup_menu(update.get_bot())
        await cmd_settings(update, None, edit=True)


async def cmd_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    if not await _guard(update):
        return
    user = _user(update)
    await _show(update, *render_profile(_lang(update, user), user.get("profile") or {}, _athlete_inputs(update)),
                edit=edit)


async def _on_profile_button(update: Update, rest: str):
    lang = _lang(update)
    action, _, field = rest.partition(":")
    if action == "clear":
        if await _stored(update, lang, lambda: _store().clear_profile(_uid(update))):
            await _reply(update, t(lang, "prof.cleared"))
            await cmd_profile(update, None)
    elif action == "edit" and field in PROFILE_FIELDS:
        pending = {"kind": "profile", "field": field}
        if await _stored(update, lang, lambda: _store().set_pending(_uid(update), pending)):
            await _reply(update, ask_text(lang, pending))


async def cmd_goal(update: Update, ctx: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    if not await _guard(update):
        return
    lang = _lang(update)
    try:
        goal = _store().get_goal(_uid(update))
    except Exception as e:  # noqa: BLE001
        logger.error("goal read failed: %s", e)
        goal = None
    await _show(update, *render_goal(lang, goal, date.today()), edit=edit)


async def _on_goal_button(update: Update, rest: str):
    lang = _lang(update)
    action, _, value = rest.partition(":")
    uid = _uid(update)
    if action == "new":
        await _reply(update, t(lang, "goal.ask.distance"), reply_markup=distance_keyboard(lang))
    elif action == "delete":
        if await _stored(update, lang, lambda: _store().delete_goal(uid)):
            await _reply(update, t(lang, "goal.deleted"))
    elif action == "dist":
        km = DISTANCES.get(value)
        pending = ({"kind": "goal", "step": "date", "data": {"distance_km": km}} if km
                   else {"kind": "goal", "step": "distance_other", "data": {}})
        if await _stored(update, lang, lambda: _store().set_pending(uid, pending)):
            await _reply(update, ask_text(lang, pending))


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    user = _user(update)
    lang = _lang(update, user)
    if not user.get("pending"):
        await _reply(update, t(lang, "nothing_to_cancel"))
        return
    if await _stored(update, lang, lambda: _store().set_pending(_uid(update), None)):
        await _reply(update, t(lang, "cancelled"))


# ---------- data commands ----------

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _guard(update):
        return
    lang = _lang(update)
    bq = _bq(update)
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
    acts = _bq(update).get_week_activities()
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
    rows = _bq(update).get_source_status()
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

async def _send_ai_text(update: Update, markdown: str):
    """Claude's Markdown -> Telegram HTML, split to fit; plain text if Telegram rejects the markup."""
    for chunk in split_message(md_to_html(markdown)):
        try:
            await _reply(update, chunk, parse_mode="HTML")
        except BadRequest as e:
            logger.warning("HTML rejected by Telegram (%s), sending this part as plain text", e)
            await _reply(update, html_to_plain(chunk))


def _record_usage(update: Update, answer) -> None:
    try:
        _store().add_usage(_uid(update), _month(), answer.input_tokens, answer.output_tokens, answer.cost_usd)
    except Exception as e:  # noqa: BLE001 - spending stats must never block the answer
        logger.error("usage write failed: %s", e)


async def _ai_reply(update: Update, prompt: str, label: str, task: str, **fmt):
    user = _user(update)
    lang = _lang(update, user)
    mode = (user.get("settings") or {}).get("mode", DEFAULT_MODE)
    try:
        goal = _store().get_goal(_uid(update))
    except Exception as e:  # noqa: BLE001
        logger.error("goal read failed: %s", e)
        goal = None
    profile = user.get("profile") or {}
    inputs = _athlete_inputs(update)
    lthr_then = ((inputs["lthr_then"], str(inputs["lthr_then_date"]))
                 if inputs.get("lthr_then") and inputs.get("lthr_then_date") else None)
    athlete = athlete_note(profile, goal, date.today(),
                           zones=compute_zones(profile, inputs, inputs.get("resting_hr_7d")),
                           garmin_resting_hr=inputs.get("resting_hr_7d"), lthr_then=lthr_then)
    await _reply(update, t(lang, "ai.working", label=t(lang, f"ai.label.{label}")))
    try:
        answer = analyze(_llm(), prompt, _context(_bq(update)), task, lang, mode=mode, athlete=athlete, **fmt)
        _record_usage(update, answer)
        if not answer.text:
            await _reply(update, t(lang, "ai.error"))
            return
        await _send_ai_text(update, answer.text)
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
    """An answer to a pending question (profile/goal input), otherwise a free-form question."""
    if not await _guard(update):
        return
    user = _user(update)
    pending = user.get("pending")
    if pending:
        lang = _lang(update, user)
        try:
            watch = _athlete_inputs(update) if pending.get("kind") == "profile" else None
            text, kb = handle_input(_store(), _uid(update), lang, pending, update.message.text, date.today(),
                                    watch)
        except Exception as e:  # noqa: BLE001
            logger.error("input handling failed: %s", e)
            await _reply(update, t(lang, "storage.error"))
            return
        await _show(update, text, kb)
        return
    r = route_message(update.message.text, _lang(update, user), date.today())
    if r.kind != "model":
        try:
            answer = _fast_answer(update, r, user)
        except Exception as e:  # noqa: BLE001 - any failure falls back to the model
            logger.error("fast answer %s failed: %s", r.key, e)
            answer = None
        logger.info("route kind=%s key=%s answered_without_ai=%s", r.kind, r.key, answer is not None)
        if answer is not None:
            text, kw = answer
            await _reply(update, text, **kw)
            return
    else:
        logger.info("route kind=model")
    await _ai_reply(update, ASK_PROMPT, "ask", "chat", question=update.message.text[:1000])


# ---------- answers without the AI model (filter and rule router) ----------

def _d(v) -> str:
    return v.strftime("%d.%m") if hasattr(v, "strftime") else str(v)


def _clock(seconds: float) -> str:
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _fast_metric(update: Update, lang: str, metric: str, day: date) -> str:
    if metric == "vo2max":
        load = _bq(update).get_load_last_days(1)
        v = load[-1].get("vo2max") if load else None
        return t(lang, "fast.metric.vo2max", v=f"{v:.1f}", date=_d(load[-1]["date"])) if v else t(lang, "fast.no_data")
    rows = _bq(update).get_health_last_days(4)
    row = next((h for h in rows if str(h.get("date")) == day.isoformat()), None) or {}
    when = _d(day)
    if metric == "resting_hr" and row.get("resting_hr"):
        return t(lang, "fast.metric.resting_hr", date=when, v=row["resting_hr"])
    if metric == "hrv" and row.get("hrv_last_night"):
        extra = (t(lang, "fast.metric.hrv_extra", lo=row["hrv_baseline_low"], hi=row["hrv_baseline_high"],
                   status=row.get("hrv_status") or "—") if row.get("hrv_baseline_low") else "")
        return t(lang, "fast.metric.hrv", date=when, v=row["hrv_last_night"], extra=extra)
    if metric == "sleep" and row.get("sleep_seconds"):
        extra = t(lang, "fast.metric.sleep_extra", deep=_hm(row.get("deep_sleep_seconds"), lang),
                  rem=_hm(row.get("rem_sleep_seconds"), lang), score=row.get("sleep_score") or "—")
        return t(lang, "fast.metric.sleep", date=when, v=_hm(row["sleep_seconds"], lang), extra=extra)
    if metric == "steps" and row.get("steps") is not None:
        return t(lang, "fast.metric.steps", date=when, v=row["steps"])
    if metric == "body_battery" and row.get("body_battery_high") is not None:
        return t(lang, "fast.metric.body_battery", date=when, lo=row.get("body_battery_low") or "—",
                 hi=row["body_battery_high"])
    if metric == "stress" and row.get("stress_avg") is not None:
        return t(lang, "fast.metric.stress", date=when, v=row["stress_avg"])
    return t(lang, "fast.metric.missing", date=when)


def _fast_answer(update: Update, r: Route, user: dict) -> tuple[str, dict] | None:
    """Text and reply options for messages answered without the model; None = ask the model."""
    lang, p, html = r.lang, r.params, {"parse_mode": "HTML"}
    if r.kind == "filter":
        return t(lang, r.key), {}
    if r.key == "fact.last_workout":
        a = _bq(update).get_last_activity()
        return (_fmt_activity(a, lang), {"parse_mode": "Markdown"}) if a else (t(lang, "fast.no_data"), {})
    if r.key == "fact.records":
        return analytics.render_records(lang, _bq(update).get_records(date.today() - timedelta(days=365))), html
    if r.key == "fact.run_distance":
        per = _bq(update).get_period(p["start"], p["end"])
        fields = dict(period=t(lang, f"fast.period.{p['period']}"), start=_d(p["start"]), end=_d(p["end"]))
        if not per.get("runs"):
            return t(lang, "fast.run_none", **fields), html
        return t(lang, "fast.run_distance", km=f"{per['run_km']:.1f}", runs=per["runs"], **fields), html
    if r.key == "fact.metric":
        return _fast_metric(update, lang, p["metric"], p["date"]), html
    if r.key == "calc.zones":
        inputs = _athlete_inputs(update)
        z = compute_zones(user.get("profile") or {}, inputs, inputs.get("resting_hr_7d"))
        if not z:
            return t(lang, "prof.zones.need"), html
        lines = [_zones_title(lang, z)] + [f"Z{i + 1}: {a}–{b}" for i, (a, b) in enumerate(z.zones)]
        return "\n".join(lines), html
    if r.key == "calc.pace":
        km = p["distance_km"]
        km_s = f"{km:g}"
        if "total_s" in p:
            return t(lang, "fast.pace.result", km=km_s, time=_clock(p["total_s"]), pace=_clock(p["total_s"] / km)), html
        return t(lang, "fast.time.result", km=km_s, pace=_clock(p["pace_s"]), time=_clock(p["pace_s"] * km)), html
    return None


def _clears_pending(handler):
    """A command interrupts any half-finished input, so the next text isn't taken as an answer."""
    async def wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if _allowed(update) and _user(update).get("pending"):
            try:
                _store().set_pending(_uid(update), None)
            except Exception as e:  # noqa: BLE001
                logger.error("state write failed: %s", e)
        await handler(update, ctx)
    return wrapped


COMMANDS = {
    "menu": cmd_menu, "today": cmd_today, "week": cmd_week, "coach": cmd_coach,
    "health": cmd_health, "nutrition": cmd_nutrition, "goal": cmd_goal, "profile": cmd_profile,
    "settings": cmd_settings, "status": cmd_status, "records": cmd_records, "month": cmd_month,
    "compare": cmd_compare, "charts": cmd_charts,
}
# What Telegram's command handlers call (button presses go through COMMANDS directly).
COMMAND_HANDLERS = {**{k: _clears_pending(v) for k, v in COMMANDS.items()}, "cancel": cmd_cancel}

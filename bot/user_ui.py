"""Screens for /settings, /profile and /goal, and the step-by-step text input behind them.

Pure functions: they take state, return (text, keyboard) and never call Telegram,
so they are easy to test. Handlers in handlers.py do the I/O.
"""

from __future__ import annotations

from datetime import date

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

from athlete import (DISTANCES, PROFILE_FIELDS, InvalidInput, active_goal, fmt_duration, fmt_km, fmt_pace,
                     hr_zones, parse_date, parse_distance, parse_duration, parse_profile_value)
from i18n import SUPPORTED, t
from models import DEFAULT_MODE, MODES
from state import StateStore

LANG_CHOICES = ("auto", *SUPPORTED)


def _mark(label: str, on: bool) -> str:
    return f"✓ {label}" if on else label


# ---------- settings ----------

def render_settings(lang: str, settings: dict, usage: dict) -> tuple[str, Kb]:
    cur_lang = settings.get("lang", "auto")
    mode = settings.get("mode", DEFAULT_MODE)
    morning = bool(settings.get("morning"))
    lines = [
        f"<b>{t(lang, 'set.title')}</b>", "",
        t(lang, "set.lang", v=t(lang, f"set.lang.{cur_lang}")),
        t(lang, "set.mode", v=t(lang, f"set.mode.{mode}")),
        t(lang, "set.morning", v=t(lang, "set.on" if morning else "set.off"))
        + (" " + t(lang, "set.morning.soon") if morning else ""),
        "",
        (t(lang, "set.usage", cost=f"{usage.get('cost_usd', 0):.2f}", calls=usage.get("calls", 0))
         if usage.get("calls") else t(lang, "set.usage.none")),
        "", t(lang, "set.mode.help"), "", t(lang, "set.answer_lang"),
    ]
    kb = Kb([
        [Btn(_mark(t(lang, f"set.lang.{c}"), c == cur_lang), callback_data=f"set:lang:{c}") for c in LANG_CHOICES],
        [Btn(_mark(t(lang, f"set.mode.{m}"), m == mode), callback_data=f"set:mode:{m}") for m in MODES],
        [Btn(t(lang, "set.btn.morning", v=t(lang, "set.on" if morning else "set.off")),
             callback_data=f"set:morning:{'off' if morning else 'on'}")],
    ])
    return "\n".join(lines), kb


def apply_setting(store: StateStore, chat_id: int, key: str, value: str) -> bool:
    """Validates and stores one setting from a button. False if the button data is unknown."""
    if key == "lang" and value in LANG_CHOICES:
        store.update_settings(chat_id, {"lang": value})
    elif key == "mode" and value in MODES:
        store.update_settings(chat_id, {"mode": value})
    elif key == "morning" and value in ("on", "off"):
        store.update_settings(chat_id, {"morning": value == "on"})
    else:
        return False
    return True


# ---------- profile ----------

def render_profile(lang: str, profile: dict) -> tuple[str, Kb]:
    lines = [f"<b>{t(lang, 'prof.title')}</b>", ""]
    for f in PROFILE_FIELDS:
        v = profile.get(f)
        shown = "—" if v in (None, "") else f"{v}{t(lang, 'prof.unit.' + f) if f == 'weight_kg' else ''}"
        lines.append(t(lang, "prof.line", name=t(lang, f"prof.{f}"), v=_esc(shown)))
    lines.append("")
    zones = hr_zones(profile)
    if zones:
        method = "karvonen" if profile.get("resting_hr") else "max"
        lines.append(t(lang, "prof.zones", method=t(lang, f"prof.zones.{method}")))
        lines += [f"Z{i + 1}: {a}–{b}" for i, (a, b) in enumerate(zones)]
    else:
        lines.append(t(lang, "prof.zones.need"))
    lines += ["", t(lang, "prof.footer")]
    fields = list(PROFILE_FIELDS)
    rows = [[Btn(t(lang, f"prof.{f}"), callback_data=f"prof:edit:{f}") for f in fields[i:i + 2]]
            for i in range(0, len(fields), 2)]
    if profile:
        rows.append([Btn(t(lang, "prof.btn.clear"), callback_data="prof:clear")])
    return "\n".join(lines), Kb(rows)


# ---------- goal ----------

def render_goal(lang: str, goal: dict | None, today: date) -> tuple[str, Kb]:
    g = active_goal(goal, today)
    if not g:
        return t(lang, "goal.none"), Kb([[Btn(t(lang, "goal.btn.new"), callback_data="goal:new")]])
    d = date.fromisoformat(g["date"])
    days = (d - today).days
    lines = [
        "<b>" + t(lang, "goal.name", name=_esc(g.get("name") or t(lang, "goal.unnamed"))) + "</b>",
        t(lang, "goal.distance", km=fmt_km(g["distance_km"])),
        t(lang, "goal.date", date=d.strftime("%d.%m.%Y"), days=days, weeks=days // 7),
        (t(lang, "goal.target", time=fmt_duration(g["target_s"]), pace=fmt_pace(g["target_s"] / g["distance_km"]))
         if g.get("target_s") else t(lang, "goal.no_target")),
    ]
    kb = Kb([[Btn(t(lang, "goal.btn.edit"), callback_data="goal:new"),
              Btn(t(lang, "goal.btn.delete"), callback_data="goal:delete")]])
    return "\n".join(lines), kb


def distance_keyboard(lang: str) -> Kb:
    keys = list(DISTANCES) + ["other"]
    return Kb([[Btn(t(lang, f"goal.dist.{k}"), callback_data=f"goal:dist:{k}") for k in keys[i:i + 3]]
               for i in range(0, len(keys), 3)])


# ---------- step-by-step input ----------

def ask_text(lang: str, pending: dict) -> str:
    if pending["kind"] == "profile":
        return t(lang, f"prof.ask.{pending['field']}") + "\n" + t(lang, "ask.hint")
    return t(lang, f"goal.ask.{pending['step']}") + ("" if pending["step"] in ("target", "name") else "\n/cancel")


def handle_input(store: StateStore, chat_id: int, lang: str, pending: dict, text: str,
                 today: date) -> tuple[str, Kb | None]:
    """Applies one text answer to the pending question. Returns the next screen or question."""
    try:
        if pending["kind"] == "profile":
            field = pending["field"]
            store.update_profile(chat_id, {field: parse_profile_value(field, text)})
            store.set_pending(chat_id, None)
            return render_profile(lang, store.get_user(chat_id).get("profile", {}))

        data = dict(pending.get("data", {}))
        step = pending["step"]
        if step == "distance_other":
            data["distance_km"] = parse_distance(text)
            nxt = "date"
        elif step == "date":
            data["date"] = parse_date(text, today).isoformat()
            nxt = "target"
        elif step == "target":
            data["target_s"] = parse_duration(text, data["distance_km"])
            nxt = "name"
        else:  # name
            name = text.strip()
            data["name"] = None if name.lower() in {"-", "—"} else name[:80]
            store.set_goal(chat_id, data)
            store.set_pending(chat_id, None)
            body, kb = render_goal(lang, data, today)
            return t(lang, "goal.saved") + "\n\n" + body, kb
        new_pending = {"kind": "goal", "step": nxt, "data": data}
        store.set_pending(chat_id, new_pending)
        return ask_text(lang, new_pending), None
    except InvalidInput as e:
        return t(lang, e.key, **e.kw) + "\n\n" + ask_text(lang, pending), None


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

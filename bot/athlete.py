"""Athlete profile and goal: input parsing, heart-rate zones, the note handed to Claude."""

from __future__ import annotations

import re
from datetime import date, timedelta

# field -> (min, max, kind); kind "int" | "float" | "text"
PROFILE_FIELDS = {
    "age": (10, 100, "int"),
    "weight_kg": (30, 250, "float"),
    "max_hr": (120, 230, "int"),
    "resting_hr": (30, 100, "int"),
    "injuries": (0, 300, "text"),
}

DISTANCES = {"5k": 5.0, "10k": 10.0, "half": 21.0975, "marathon": 42.195}

CLEAR_WORDS = {"-", "—", "нет", "no", "none"}


class InvalidInput(ValueError):
    """Value can't be used; the message key explains why."""

    def __init__(self, key: str, **kw):
        super().__init__(key)
        self.key, self.kw = key, kw


def parse_profile_value(field: str, raw: str):
    """Returns the value to store, or None to clear the field."""
    lo, hi, kind = PROFILE_FIELDS[field]
    raw = raw.strip()
    if raw.lower() in CLEAR_WORDS:
        return None
    if kind == "text":
        if len(raw) > hi:
            raise InvalidInput("in.too_long", n=hi)
        return raw
    try:
        num = float(raw.replace(",", "."))
    except ValueError:
        raise InvalidInput("in.not_number") from None
    if not lo <= num <= hi:
        raise InvalidInput("in.range", lo=lo, hi=hi)
    return int(round(num)) if kind == "int" else round(num, 1)


def parse_distance(raw: str) -> float:
    try:
        km = float(raw.strip().lower().replace(",", ".").replace("km", "").replace("км", "").strip())
    except ValueError:
        raise InvalidInput("in.not_number") from None
    if not 1 <= km <= 400:
        raise InvalidInput("in.range", lo=1, hi=400)
    return round(km, 3)


def parse_date(raw: str, today: date) -> date:
    raw = raw.strip()
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", raw) or None
    try:
        d = date(int(m[3]), int(m[2]), int(m[1])) if m else date.fromisoformat(raw)
    except ValueError:
        raise InvalidInput("in.bad_date") from None
    if d <= today:
        raise InvalidInput("in.date_past")
    if d > today + timedelta(days=730):
        raise InvalidInput("in.date_far")
    return d


def parse_duration(raw: str, distance_km: float) -> int | None:
    """Target time: 'h:mm:ss' or a two-part 'a:b' read as mm:ss, or as h:mm when mm:ss
    would mean a pace faster than 2:00/km (e.g. '3:30' for a marathon). '-' -> None."""
    raw = raw.strip()
    if raw.lower() in CLEAR_WORDS:
        return None
    parts = raw.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise InvalidInput("in.bad_time")
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        h, m, s = nums
        total = h * 3600 + m * 60 + s if m < 60 and s < 60 else -1
    else:
        a, b = nums
        total = a * 60 + b if b < 60 else -1
        if total >= 0 and total / distance_km < 120 and b < 60:
            total = a * 3600 + b * 60
    pace = total / distance_km if total > 0 else 0
    if not 120 <= pace <= 15 * 60:
        raise InvalidInput("in.bad_time")
    return total


def fmt_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(seconds_per_km: float) -> str:
    m, s = divmod(int(round(seconds_per_km)), 60)
    return f"{m}:{s:02d}"


def fmt_km(km: float) -> str:
    return f"{km:.1f}".rstrip("0").rstrip(".")


def hr_zones(profile: dict) -> list[tuple[int, int]] | None:
    """Five zones. With resting HR: Karvonen (% of heart-rate reserve), else % of max HR."""
    hr_max = profile.get("max_hr")
    if not hr_max:
        return None
    rest = profile.get("resting_hr")
    bounds = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    if rest:
        pts = [round(rest + b * (hr_max - rest)) for b in bounds]
    else:
        pts = [round(b * hr_max) for b in bounds]
    return [(pts[i], pts[i + 1]) for i in range(5)]


def active_goal(goal: dict | None, today: date) -> dict | None:
    if not goal or not goal.get("date"):
        return None
    return goal if date.fromisoformat(goal["date"]) >= today else None


def athlete_note(profile: dict, goal: dict | None, today: date) -> str:
    """English block for Claude's prompt: who the athlete is and what they train for."""
    lines = []
    facts = []
    if profile.get("age"):
        facts.append(f"age {profile['age']}")
    if profile.get("weight_kg"):
        facts.append(f"weight {profile['weight_kg']} kg")
    if profile.get("max_hr"):
        facts.append(f"max HR {profile['max_hr']}")
    if profile.get("resting_hr"):
        facts.append(f"resting HR {profile['resting_hr']} (entered by the athlete)")
    lines.append("Athlete profile: " + (", ".join(facts) if facts else "not filled in") + ".")
    zones = hr_zones(profile)
    if zones:
        method = "Karvonen, % of HR reserve" if profile.get("resting_hr") else "% of max HR"
        lines.append(f"HR zones ({method}): " + ", ".join(f"Z{i + 1} {a}–{b}" for i, (a, b) in enumerate(zones)) + ".")
    if profile.get("injuries"):
        lines.append(f"Injuries / limitations: {profile['injuries']}. Respect them in any advice.")

    g = active_goal(goal, today)
    if g:
        d = date.fromisoformat(g["date"])
        days = (d - today).days
        part = (f"Goal: {g.get('name') or 'race'} — {fmt_km(g['distance_km'])} km on {d.isoformat()}, "
                f"in {days} days (~{days // 7} weeks)")
        if g.get("target_s"):
            part += (f", target time {fmt_duration(g['target_s'])} "
                     f"(pace {fmt_pace(g['target_s'] / g['distance_km'])}/km)")
        lines.append(part + ". Plan advice around this goal and the time left.")
    else:
        lines.append("No race goal is set: treat this as ongoing training (consistency, general fitness, staying healthy).")
    return "\n".join(lines)

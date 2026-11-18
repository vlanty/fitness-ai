"""Athlete profile and goal: input parsing, heart-rate zones, the note handed to Claude."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

# field -> (min, max, kind); kind "int" | "float" | "text"
PROFILE_FIELDS = {
    "age": (10, 100, "int"),
    "weight_kg": (30, 250, "float"),
    "max_hr": (120, 230, "int"),
    "resting_hr": (30, 100, "int"),
    "lthr": (120, 210, "int"),       # lactate threshold HR, used when the watch's zones are unknown
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


# Joe Friel's running zones as % of lactate threshold HR (Z5 runs up to max HR).
LTHR_BOUNDS = (0.65, 0.85, 0.90, 0.95, 1.00)


@dataclass
class Zones:
    """Five HR zones and where they came from.

    method: "watch" (the athlete's Garmin, from .FIT), "lthr" (threshold from the profile),
    "karvonen" (% of HR reserve) or "max" (% of max HR).
    """
    zones: list[tuple[int, int]]
    method: str
    watch_calc: str | None = None    # watch only: percent_lthr / percent_max_hr / percent_hrr
    lthr: int | None = None
    as_of: str | None = None         # watch only: date of the activity the zones come from
    resting_hr: int | None = None    # karvonen only
    resting_from_garmin: bool = False


def watch_zones(row: dict | None) -> Zones | None:
    """Zones from the latest activity's .FIT (bq_reader.get_athlete_inputs row)."""
    try:
        bounds = [int(x) for x in (row or {}).get("hr_zone_bounds", "").split(",") if x.strip()]
    except (AttributeError, ValueError):
        return None
    if len(bounds) < 6 or bounds[:6] != sorted(bounds[:6]):
        return None
    as_of = row.get("zones_date")
    return Zones([(bounds[i - 1], bounds[i]) for i in range(1, 6)], "watch", row.get("hr_zone_calc"),
                 row.get("lthr"), as_of.isoformat() if hasattr(as_of, "isoformat") else as_of)


def compute_zones(profile: dict, watch: dict | None = None, garmin_resting_hr: int | None = None) -> Zones | None:
    """Best available zones: watch -> threshold in profile -> Karvonen -> % of max HR."""
    z = watch_zones(watch)
    if z:
        return z
    hr_max = profile.get("max_hr")
    lthr = profile.get("lthr")
    if lthr:
        pts = [round(p * lthr) for p in LTHR_BOUNDS] + [hr_max or round(1.06 * lthr)]
        return Zones([(pts[i], pts[i + 1]) for i in range(5)], "lthr", lthr=lthr)
    if not hr_max:
        return None
    rest = profile.get("resting_hr") or garmin_resting_hr
    bounds = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    if rest:
        pts = [round(rest + b * (hr_max - rest)) for b in bounds]
        return Zones([(pts[i], pts[i + 1]) for i in range(5)], "karvonen",
                     resting_hr=rest, resting_from_garmin=not profile.get("resting_hr"))
    pts = [round(b * hr_max) for b in bounds]
    return Zones([(pts[i], pts[i + 1]) for i in range(5)], "max")


WATCH_CALC = {"percent_lthr": "% of lactate threshold HR", "percent_max_hr": "% of max HR",
              "percent_hrr": "% of HR reserve"}


def _zones_note(z: Zones) -> str:
    listed = ", ".join(f"Z{i + 1} {a}–{b}" for i, (a, b) in enumerate(z.zones))
    if z.method == "watch":
        how = WATCH_CALC.get(z.watch_calc or "", z.watch_calc or "unknown method")
        return (f"HR zones from the athlete's Garmin watch ({how}"
                + (f", LTHR {z.lthr}" if z.lthr else "") + f", as of {z.as_of}): {listed}. "
                "Garmin's per-activity time in zone uses these same zones.")
    if z.method == "lthr":
        return f"HR zones from the lactate threshold HR {z.lthr} entered by the athlete (Friel): {listed}."
    if z.method == "karvonen":
        src = "Garmin 7-day average" if z.resting_from_garmin else "entered by the athlete"
        return f"HR zones (Karvonen, % of HR reserve, resting HR {z.resting_hr} — {src}): {listed}."
    return f"HR zones (% of max HR): {listed}."


def active_goal(goal: dict | None, today: date) -> dict | None:
    if not goal or not goal.get("date"):
        return None
    return goal if date.fromisoformat(goal["date"]) >= today else None


def athlete_note(profile: dict, goal: dict | None, today: date, zones: Zones | None = None,
                 garmin_resting_hr: int | None = None, lthr_then: tuple[int, str] | None = None) -> str:
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
    if garmin_resting_hr:
        facts.append(f"resting HR {garmin_resting_hr} (Garmin, 7-day average)")
    if profile.get("lthr"):
        facts.append(f"lactate threshold HR {profile['lthr']} (entered by the athlete)")
    lines.append("Athlete profile: " + (", ".join(facts) if facts else "not filled in") + ".")
    if zones:
        lines.append(_zones_note(zones))
        if zones.method == "watch" and zones.lthr and lthr_then and lthr_then[0] != zones.lthr:
            lines.append(f"Lactate threshold HR trend (watch): {lthr_then[0]} on {lthr_then[1]} -> {zones.lthr} now.")
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

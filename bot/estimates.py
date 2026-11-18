"""fitness-ai's own estimates of readiness and training status.

Garmin computes Training Readiness and Training Status with closed Firstbeat
models and the Personal API does not return them. Garmin documents only the
inputs, so these are transparent rule-based approximations from the same
inputs. They are always labelled as our estimate, never as Garmin's value.

Readiness inputs (Garmin lists six; recovery time is not available to us):
  last night's sleep score, sleep over 3 nights, HRV vs personal baseline,
  acute load ratio, stress over 3 days.
Training status inputs (Garmin): VO2 max trend, acute load, HRV status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from i18n import t

READINESS_BANDS = [(95, "excellent"), (75, "high"), (50, "moderate"), (25, "low"), (0, "poor")]
READINESS_WEIGHTS = {"sleep": 0.25, "sleep_3n": 0.15, "hrv": 0.25, "load": 0.20, "stress_3d": 0.15}


def _clip(x: float) -> float:
    return max(0.0, min(100.0, x))


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


@dataclass
class Estimate:
    value: str
    score: int | None = None
    factors: list[str] = field(default_factory=list)
    lang: str = "en"

    def text(self, title: str) -> str:
        head = f"{title}: {self.score} — {self.value}" if self.score is not None else f"{title}: {self.value}"
        return f"{head} ({t(self.lang, 'est.label')})" + ("\n  " + "; ".join(self.factors) if self.factors else "")


def hrv_score(last: float | None, low: float | None, high: float | None) -> float | None:
    """100 inside the personal balanced range, falls steeply below it, slightly lower above it."""
    if last is None or low is None or high is None or low <= 0:
        return None
    if last < low:
        return _clip(100 - (low - last) / low * 400)  # 25% below the range -> 0
    if last > high:
        return 85.0
    return 100.0


def load_score(ratio: float | None) -> float | None:
    """Garmin's optimal band is 0.8–1.4; low load means fresh, above 1.4 falls to 0 at 2.0."""
    if ratio is None:
        return None
    if ratio <= 1.4:
        return 100.0
    return _clip(100 - (ratio - 1.4) / 0.6 * 100)


def stress_score(avg_stress: float | None) -> float | None:
    """Garmin stress 0–25 is rest; every point above costs 2."""
    if avg_stress is None:
        return None
    return _clip(100 - max(0.0, avg_stress - 25) * 2)


def readiness(health: list[dict], load: dict | None, lang: str = "en") -> Estimate:
    """health: health_daily_unified rows ordered by date (latest last); load: latest training_load_daily row."""
    if not health:
        return Estimate(t(lang, "est.no_data"), lang=lang)
    today, last3 = health[-1], health[-3:]
    ratio = load.get("load_ratio") if load and load.get("load_ratio_zone") else None
    parts = {
        "sleep": today.get("sleep_score"),
        "sleep_3n": _avg(h.get("sleep_score") for h in last3),
        "hrv": hrv_score(today.get("hrv_last_night"), today.get("hrv_baseline_low"), today.get("hrv_baseline_high")),
        "load": load_score(ratio),
        "stress_3d": stress_score(_avg(h.get("stress_avg") for h in last3)),
    }
    have = {k: v for k, v in parts.items() if v is not None}
    if len(have) < 3:
        return Estimate(t(lang, "est.little_data"), factors=[t(lang, "est.have_factors", n=len(have))], lang=lang)
    total = sum(READINESS_WEIGHTS[k] for k in have)
    score = round(sum(v * READINESS_WEIGHTS[k] for k, v in have.items()) / total)
    band = t(lang, "est.band." + next(name for floor, name in READINESS_BANDS if score >= floor))
    names = {k: t(lang, "est.f." + k) for k in parts}
    factors = [f"{names[k]} {round(v)}" for k, v in have.items()]
    missing = [names[k] for k in parts if k not in have]
    if missing:
        factors.append(t(lang, "est.missing", list=", ".join(missing)))
    factors.append(t(lang, "est.no_recovery"))
    return Estimate(band, score, factors, lang)


def training_status(load: dict | None, health: list[dict], today: date | None = None, lang: str = "en") -> Estimate:
    """Rule-based approximation of Garmin's training status levels."""
    if not load or not load.get("chronic_load"):
        return Estimate(t(lang, "est.no_data"), factors=[t(lang, "est.need_history")], lang=lang)
    today = today or load.get("date") or date.today()
    ratio, zone = load.get("load_ratio"), load.get("load_ratio_zone")
    vo2, vo2_old = load.get("vo2max"), load.get("vo2max_28d_ago")
    trend = None if vo2 is None or vo2_old is None else vo2 - vo2_old
    hrv_status = (health[-1].get("hrv_status") if health else None) or ""
    last_act = load.get("last_activity_date")
    idle_days = (today - last_act).days if last_act else None

    factors = [f"load ratio {ratio:.2f} ({zone})" if ratio is not None and zone else "load ratio —",
               t(lang, "est.vo2_trend", v=f"{vo2:.1f}", d=f"{trend:+.1f}") if trend is not None else t(lang, "est.vo2_none"),
               f"HRV status {hrv_status or '—'}"]

    if idle_days is not None and idle_days >= 7:
        value = "detraining"
    elif ratio is not None and ratio > 1.5:
        value = "overreaching"
    elif hrv_status in ("LOW", "POOR", "UNBALANCED") and (ratio or 0) >= 0.8:
        value = "strained"
    elif trend is not None and trend >= 1 and zone == "optimal":
        value = "productive"
    elif trend is not None and trend <= -1 and (ratio or 0) >= 0.8:
        value = "unproductive"
    elif zone == "low":
        value = "recovery"
    else:
        value = "maintaining"
    return Estimate(t(lang, "est.st." + value), factors=factors, lang=lang)

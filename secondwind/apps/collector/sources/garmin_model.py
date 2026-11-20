"""Garmin payloads -> the universal data model (observations, sessions, zones, streams).

Works on the raw JSON Garmin returned (the same dicts the collector stores), so live syncs and
re-processing of stored payloads go through exactly this code. Every mapped field is listed in
the tables below; numeric activity fields not listed are kept as `x.garmin.activity.<field>`.
Personal fields (owner name, profile images, coordinates) are not copied into observations;
they stay only in the raw payload.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime, timedelta, timezone

from secondwind.core.observations import ModelBatch, observation, stable_id
from secondwind.core.registry import registry

logger = logging.getLogger(__name__)
SOURCE = "garmin"
RUN_TYPES = ("running", "trail_running", "treadmill_running", "track_running", "street_running")

# (endpoint, path in that endpoint's daily record, metric, method, unit, context)
HEALTH_FIELDS = [
    ("sleep", "sleepTimeSeconds", "sleep.duration.total", "vendor_sleep_staging", "s", "sleep"),
    ("sleep", "deepSleepSeconds", "sleep.duration.deep", "vendor_sleep_staging", "s", "sleep"),
    ("sleep", "lightSleepSeconds", "sleep.duration.light", "vendor_sleep_staging", "s", "sleep"),
    ("sleep", "remSleepSeconds", "sleep.duration.rem", "vendor_sleep_staging", "s", "sleep"),
    ("sleep", "awakeSleepSeconds", "sleep.duration.awake", "vendor_sleep_staging", "s", "sleep"),
    ("sleep", "sleepScores.overall.value", "vendor_score.garmin.sleep_score", "vendor_native", "{score}", "sleep"),
    ("hrv", "lastNightAvg", "hrv.night", "rmssd_sleep_night_mean", "ms", "sleep"),
    ("hrv", "weeklyAvg", "hrv.night_7d_mean", "rmssd_sleep_night_mean", "ms", "sleep"),
    ("hrv", "lastNight5MinHigh", "hrv.night_5min_high", "rmssd_sleep_night_mean", "ms", "sleep"),
    ("hrv", "baseline.balancedLow", "hrv.baseline_low", "garmin_hrv_baseline", "ms", "sleep"),
    ("hrv", "baseline.balancedUpper", "hrv.baseline_high", "garmin_hrv_baseline", "ms", "sleep"),
    ("hrv", "status", "vendor_score.garmin.hrv_status", "vendor_native", "{category}", "sleep"),
    ("summary", "heartRate.restingValue", "heart.resting", "lowest_30min_mean_24h", "/min", "all_day"),
    ("summary", "stress.avgLevel", "vendor_score.garmin.stress_mean", "vendor_native", "{score}", "all_day"),
    ("summary", "bodyBattery.maxValue", "vendor_score.garmin.body_battery_max", "vendor_native", "{score}", "all_day"),
    ("summary", "bodyBattery.minValue", "vendor_score.garmin.body_battery_min", "vendor_native", "{score}", "all_day"),
    ("summary", "bodyBattery.chargedValue", "vendor_score.garmin.body_battery_charged", "vendor_native", "{score}", "all_day"),
    ("summary", "bodyBattery.drainedValue", "vendor_score.garmin.body_battery_drained", "vendor_native", "{score}", "all_day"),
    ("summary", "movement.steps.value", "activity.steps", "vendor_daily_total", "{count}", "all_day"),
    ("summary", "calories.burnedActive", "energy.active", "vendor_daily_total", "kcal", "all_day"),
    ("summary", "respiration.avgValue", "resp.rate", "vendor_daily_mean", "/min", "all_day"),
    ("vo2max", "generic.vo2MaxPreciseValue", "fitness.vo2max", "wearable_run_hr_pace", "mL/kg/min", "all_day"),
    ("vo2max", "cycling.vo2MaxPreciseValue", "fitness.vo2max_bike", "wearable_bike_hr_power", "mL/kg/min", "all_day"),
]

# (field in the activity record, metric, method, unit)
ACTIVITY_FIELDS = [
    ("distance", "workout.distance", "session_summary", "m"),
    ("elevationGain", "workout.elevation_gain", "session_summary", "m"),
    ("averageHR", "workout.hr.mean", "session_summary", "/min"),
    ("maxHR", "workout.hr.max", "session_summary", "/min"),
    ("calories", "workout.energy", "session_summary", "kcal"),
    ("aerobicTrainingEffect", "vendor_score.garmin.te_aerobic", "vendor_native", "{score}"),
    ("anaerobicTrainingEffect", "vendor_score.garmin.te_anaerobic", "vendor_native", "{score}"),
    ("trainingEffectLabel", "vendor_score.garmin.te_label", "vendor_native", "{category}"),
    ("activityTrainingLoad", "workout.load", "garmin_epoc_load", "{score}"),
    ("vO2MaxValue", "fitness.vo2max", "wearable_run_hr_pace", "mL/kg/min"),
]
# Handled separately or deliberately not copied into observations (they stay in the raw payload).
ACTIVITY_HANDLED = {f for f, *_ in ACTIVITY_FIELDS} | {
    "movingDuration", "duration", "elapsedDuration", "averageSpeed"}
PRIVATE_OR_ID = re.compile(r"^(owner.*|.*Id|.*ID|.*UUID|.*Latitude|.*Longitude|beginTimestamp|.*Timestamp.*|"
                           r"locationName|sportTypeId|userProfile.*)$")
SPLIT = re.compile(r"^fastestSplit_(\d+)$")
TIME_IN_ZONE = re.compile(r"^hrTimeInZone_(\d)$")


def dig(d, path: str):
    for part in path.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _utc(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace(" ", "T")).replace(tzinfo=timezone.utc)


def _ms(v) -> datetime | None:
    return datetime.fromtimestamp(v / 1000, tz=timezone.utc) if isinstance(v, (int, float)) else None


def health_to_model(user_id: str, day: str, parts: dict, raw_ref: str | None = None) -> ModelBatch:
    """One day of Garmin health (endpoint -> that day's record) -> observations + sleep session."""
    reg, out = registry(), ModelBatch()
    sleep = parts.get("sleep") or {}
    no_sleep = not sleep.get("sleepTimeSeconds")   # Garmin reports 0 when no sleep was recorded
    for endpoint, path, metric, method, unit, context in HEALTH_FIELDS:
        if endpoint == "sleep" and no_sleep:
            continue
        v = dig(parts.get(endpoint) or {}, path)
        if v is None or (unit != "{category}" and _num(v) is None):
            continue
        out.observations.append(observation(
            reg=reg, user_id=user_id, source=SOURCE, metric=metric, method=method, value=v, unit=unit,
            local_date=day, context=context, granularity="daily", date_rule="wake_date" if context == "sleep" else "local_calendar",
            vendor_field=f"{endpoint}.{path}", raw_ref=raw_ref))
    start, end = _ms(sleep.get("sleepStartTimestampGMT")), _ms(sleep.get("sleepEndTimestampGMT"))
    if not no_sleep and start and end:
        local = _ms(sleep.get("sleepStartTimestampLocal"))
        offset = round((local - start).total_seconds() / 60) if local else None
        out.sessions.append({
            "session_id": f"{SOURCE}:sleep:{day}", "user_id": user_id, "kind": "sleep", "source": SOURCE,
            "vendor_id": str(sleep.get("id")) if sleep.get("id") else None,
            "start_ts": start.isoformat(), "end_ts": end.isoformat(), "tz_offset_min": offset,
            "local_date": day, "is_main": True, "raw_ref": raw_ref,
            "ingested_at": datetime.now(timezone.utc).isoformat()})
    return out


def activity_to_model(user_id: str, a: dict, raw_ref: str | None = None, fit_ref: str | None = None) -> ModelBatch:
    """One Garmin activity record -> a workout session and its observations."""
    reg, out = registry(), ModelBatch()
    if not a.get("activityId"):
        return out
    vid = str(a["activityId"])
    sid = f"{SOURCE}:workout:{vid}"
    start = _utc(a.get("startTimeGMT"))
    local = _utc(a.get("startTimeLocal"))
    local_date = (a.get("startTimeLocal") or a.get("startTimeGMT") or "")[:10]
    if not local_date:
        return out
    type_key = (a.get("activityType") or {}).get("typeKey")
    moving = _num(a.get("movingDuration")) or _num(a.get("duration"))
    elapsed = _num(a.get("elapsedDuration")) or _num(a.get("duration"))
    out.sessions.append({
        "session_id": sid, "user_id": user_id, "kind": "workout", "source": SOURCE, "vendor_id": vid,
        "start_ts": start.isoformat() if start else None,
        "end_ts": (start + timedelta(seconds=elapsed)).isoformat() if start and elapsed else None,
        "tz_offset_min": round((local - start).total_seconds() / 60) if start and local else None,
        "local_date": local_date, "sport": reg.sport_for(SOURCE, type_key), "sport_vendor": type_key,
        "name": a.get("activityName"), "raw_ref": raw_ref,
        "attrs": {"fit_ref": fit_ref} if fit_ref else None,
        "ingested_at": datetime.now(timezone.utc).isoformat()})

    def add(metric, method, value, unit, field_name, attrs=None):
        out.observations.append(observation(
            reg=reg, user_id=user_id, source=SOURCE, metric=metric, method=method, value=value, unit=unit,
            local_date=local_date, session_id=sid, context="workout", granularity="session",
            date_rule="local_calendar", vendor_field=field_name, raw_ref=raw_ref, attrs=attrs))

    for field_name, metric, method, unit in ACTIVITY_FIELDS:
        v = a.get(field_name)
        if v is None or (unit != "{category}" and _num(v) is None):
            continue
        add(metric, method, v, unit, field_name)
    if moving:
        add("workout.duration.moving", "session_summary", moving, "s", "movingDuration")
    if elapsed:
        add("workout.duration.elapsed", "session_summary", elapsed, "s", "elapsedDuration")
    dist = _num(a.get("distance")) or 0.0
    if type_key in RUN_TYPES and moving and dist > 0:
        add("workout.pace.mean", "session_summary", moving / (dist / 1000), "s/km", "movingDuration/distance")

    for key, v in a.items():
        if (m := SPLIT.match(key)) and _num(v):
            add("workout.best_effort_time", "garmin_fastest_split", v, "s", key, {"distance_m": int(m.group(1))})
        elif (m := TIME_IN_ZONE.match(key)) and _num(v) is not None:
            add("workout.time_in_hr_zone", "garmin_time_in_zone", v, "s", key, {"zone": int(m.group(1))})
        elif (key not in ACTIVITY_HANDLED and not PRIVATE_OR_ID.match(key)
              and isinstance(v, (int, float)) and not isinstance(v, bool)):
            add(f"x.garmin.activity.{key}", "vendor_native", v, None, key)
    return out


# ── .FIT ──────────────────────────────────────────────────────────────────────
SEMICIRCLE = 180 / 2 ** 31


def _fit_bytes(blob: bytes) -> bytes | None:
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fit")]
            return z.read(names[0]) if names else None
    return blob


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fit_to_model(user_id: str, session_id: str, local_date: str, blob: bytes) -> ModelBatch:
    """Watch HR zones and the per-second record stream of one activity file."""
    out = ModelBatch()
    try:
        from garmin_fit_sdk import Decoder, Stream

        data = _fit_bytes(blob)
        if not data:
            return out
        msgs, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
        if errors:
            logger.info("fit %s: decode warnings: %s", session_id, errors[:2])
    except Exception as e:  # noqa: BLE001 - a broken file must never fail the sync
        logger.warning("fit %s: cannot read: %s", session_id, e)
        return out

    now = datetime.now(timezone.utc).isoformat()
    tiz = [m for m in msgs.get("time_in_zone_mesgs", []) if m.get("hr_zone_high_boundary")]
    zone = next((m for m in tiz if m.get("reference_mesg") == "session"), tiz[0] if tiz else None)
    target = (msgs.get("zones_target_mesgs") or [{}])[0]
    if zone:
        bounds = [b for b in (_int(x) for x in zone["hr_zone_high_boundary"]) if b]
        if len(bounds) >= 3 and bounds == sorted(bounds):
            calc = zone.get("hr_calc_type") or target.get("hr_calc_type")
            out.hr_zones.append({
                "user_id": user_id, "valid_from": local_date, "source": SOURCE,
                "method": str(calc) if calc is not None else "vendor_unspecified",
                "zone_count": len(bounds) - 1, "bounds": bounds,
                "anchor_lthr": _int(zone.get("threshold_heart_rate") or target.get("threshold_heart_rate")),
                "anchor_hr_max": _int(zone.get("max_heart_rate") or target.get("max_heart_rate")),
                "anchor_hr_rest": _int(zone.get("resting_heart_rate")),
                "session_id": session_id, "ingested_at": now})

    first = None
    for r in msgs.get("record_mesgs", []):
        ts = r.get("timestamp")
        if not isinstance(ts, datetime):
            continue
        ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        first = first or ts
        lat, lon = r.get("position_lat"), r.get("position_long")
        out.streams.append({
            "user_id": user_id, "session_id": session_id, "source": SOURCE, "ts": ts.isoformat(),
            "offset_s": (ts - first).total_seconds(), "hr": _int(r.get("heart_rate")),
            "speed_mps": _num(r.get("enhanced_speed", r.get("speed"))), "power_w": _num(r.get("power")),
            "cadence": _num(r.get("cadence")), "altitude_m": _num(r.get("enhanced_altitude", r.get("altitude"))),
            "lat": lat * SEMICIRCLE if isinstance(lat, (int, float)) else None,
            "lon": lon * SEMICIRCLE if isinstance(lon, (int, float)) else None,
            "distance_m": _num(r.get("distance")), "temp_c": _num(r.get("temperature")),
            "rr_ms": [], "extra": None})
    return out


def zones_session_key(row: dict) -> str:
    return stable_id(row["user_id"], row["session_id"], row["source"], row["method"])

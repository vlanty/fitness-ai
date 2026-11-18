"""Garmin Connect via the official Personal API (personal API key).

Docs: https://developer.garmin.com/gc-developer-program/personal/personal-api/
  * key -> 24h bearer token (POST .../personal-api-key/token, "ApiKey <key>");
    no refresh tokens, just ask for a new one;
  * data: GET https://personal.connectapi.garmin.com/<path>, "Bearer <token>";
  * pull only. Rate limits are not documented: requests are paced and retried
    with exponential backoff on 429/5xx (Retry-After honoured).

Endpoints and field paths were verified against real responses
(scripts/garmin_probe.py); raw responses are always kept in raw_json.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date, datetime, timedelta, timezone

import requests

from fit_zones import extract_zones

from .base import Batch, PartialFetch, RawFile, Source, to_json

logger = logging.getLogger(__name__)

BASE_URL = "https://personal.connectapi.garmin.com"
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 6
MIN_INTERVAL_S = 0.5      # pacing between requests
PAGE_SIZE = 100

RUN_TYPES = ("running", "trail_running", "treadmill_running", "track_running", "street_running")


class GarminApiError(RuntimeError):
    pass


class GarminClient:
    def __init__(self, api_key: str, base_url: str = BASE_URL, session: requests.Session | None = None,
                 sleep=time.sleep) -> None:
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.s = session or requests.Session()
        self._sleep = sleep
        self._token: str | None = None
        self._last_call = 0.0
        self.calls = 0
        self.retries = 0

    # -- auth ---------------------------------------------------------------
    def _refresh_token(self) -> None:
        r = self._send("POST", TOKEN_PATH, headers={"Accept": "*/*", "Authorization": "ApiKey " + self._key})
        if r.status_code in (401, 403):
            raise GarminApiError("Garmin отклонил ключ (HTTP %d): истёк или удалён — выпустите новый" % r.status_code)
        if not r.ok:
            raise GarminApiError(f"token: HTTP {r.status_code}")
        self._token = r.text.strip()

    # -- transport ----------------------------------------------------------
    def _send(self, method: str, path: str, **kw) -> requests.Response:
        """One logical request with pacing and retries. Never logs credentials."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.calls += 1
            try:
                r = self.s.request(method, self.base_url + path, timeout=60, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                r, err = None, type(e).__name__
            else:
                if r.status_code not in RETRY_STATUS:
                    return r
                err = f"HTTP {r.status_code}"
            if attempt == MAX_ATTEMPTS:
                break
            delay = min(60.0, 2 ** attempt) + random.uniform(0, 1)
            if r is not None and r.headers.get("Retry-After", "").isdigit():
                delay = max(delay, float(r.headers["Retry-After"]))
            self.retries += 1
            logger.warning("garmin: %s %s -> %s, повтор через %.0f с", method, path.split("?")[0], err, delay)
            self._sleep(delay)
        raise GarminApiError(f"{method} {path.split('?')[0]}: {err} после {MAX_ATTEMPTS} попыток")

    def get(self, path: str, params: dict | None = None, accept: str = "application/json") -> requests.Response:
        if self._token is None:
            self._refresh_token()
        for _ in range(2):
            r = self._send("GET", path, params=params,
                           headers={"Accept": accept, "Authorization": "Bearer " + self._token})
            if r.status_code != 401:
                return r
            self._refresh_token()  # token expired (24h) or revoked: get a new one once
        return r

    def get_json(self, path: str, params: dict | None = None):
        r = self.get(path, params)
        if r.status_code == 404 or r.status_code == 204 or not r.content:
            return None
        if not r.ok:
            raise GarminApiError(f"GET {path}: HTTP {r.status_code}")
        return r.json()


# ---- parsing helpers --------------------------------------------------------
def dig(obj, path: str):
    """Value at a dotted path, or None (e.g. dig(x, "heartRate.restingValue"))."""
    for part in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def by_date(items, date_path: str = "calendarDate") -> dict[str, dict]:
    """Index a list of per-day records by calendar date (YYYY-MM-DD)."""
    out = {}
    for it in items or []:
        day = dig(it, date_path)
        if isinstance(day, str) and len(day) >= 10:
            out[day[:10]] = it
    return out


def _int(v):
    try:
        return int(round(float(v))) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _ts_local(v):
    """Garmin '...TimestampLocal' / epoch-ms values -> ISO string (local wall time, no tz)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat()
    return str(v).replace(" ", "T")


def parse_activity(a: dict) -> dict:
    gmt = a.get("startTimeGMT")
    start_utc = (datetime.fromisoformat(gmt.replace(" ", "T")).replace(tzinfo=timezone.utc).isoformat()
                 if gmt else None)
    local = a.get("startTimeLocal") or gmt or ""
    type_key = (a.get("activityType") or {}).get("typeKey")
    dist = _float(a.get("distance")) or 0.0
    moving = _float(a.get("movingDuration")) or _float(a.get("duration"))
    pace = (moving / (dist / 1000)) if (moving and dist > 0 and type_key in RUN_TYPES) else None
    return {
        "activity_id": str(a["activityId"]),
        "source": "garmin",
        "activity_date": local[:10] if local else None,
        "start_time": start_utc,
        "name": a.get("activityName"),
        "type": type_key,
        "distance_meters": dist,
        "duration_seconds": _int(moving),
        "elapsed_seconds": _int(a.get("elapsedDuration") or a.get("duration")),
        "elevation_gain": _float(a.get("elevationGain")),
        "avg_heart_rate": _int(a.get("averageHR")),
        "max_heart_rate": _int(a.get("maxHR")),
        "avg_pace_sec_per_km": pace,
        "calories": _int(a.get("calories")),
        "aerobic_te": _float(a.get("aerobicTrainingEffect")),
        "anaerobic_te": _float(a.get("anaerobicTrainingEffect")),
        "training_load": _float(a.get("activityTrainingLoad")),
        "vo2max": _float(a.get("vO2MaxValue")),
        "training_effect_label": a.get("trainingEffectLabel"),
        "raw_json": to_json(a),
    }


# Health endpoints used per date window (4 requests), verified against real
# responses (garmin_probe 2026-11-17):
#   sleep   GET /sleep-service/sleep/dailySleepSummariesByDate  -> [ {calendarDate, ...} ]
#   hrv     GET /hrv-service/hrv/daily/{start}/{end}           -> {hrvSummaries: [ {calendarDate, ...} ]}
#   summary GET /usersummary-service/v2/dailysummaries          -> {data: [ {calendarDate, movement, heartRate, ...} ]}
#   vo2max  GET /metrics-service/metrics/maxmet/daily/{s}/{e}   -> [ {generic: {calendarDate, ...}} ]
# Not available as a daily value: SpO2 average, training readiness, training
# status, recovery time (only inside undocumented .FIT message 140).
HEALTH_MAP = {
    # column: (endpoint, path, kind)
    "sleep_start": ("sleep", "sleepStartTimestampLocal", "ts"),
    "sleep_end": ("sleep", "sleepEndTimestampLocal", "ts"),
    "sleep_seconds": ("sleep", "sleepTimeSeconds", "int"),
    "deep_sleep_seconds": ("sleep", "deepSleepSeconds", "int"),
    "light_sleep_seconds": ("sleep", "lightSleepSeconds", "int"),
    "rem_sleep_seconds": ("sleep", "remSleepSeconds", "int"),
    "awake_seconds": ("sleep", "awakeSleepSeconds", "int"),
    "sleep_score": ("sleep", "sleepScores.overall.value", "int"),
    "hrv_last_night": ("hrv", "lastNightAvg", "int"),
    "hrv_weekly_avg": ("hrv", "weeklyAvg", "int"),
    "hrv_status": ("hrv", "status", "str"),
    "hrv_5min_high": ("hrv", "lastNight5MinHigh", "int"),
    "hrv_baseline_low": ("hrv", "baseline.balancedLow", "int"),     # Garmin's personal "balanced" range
    "hrv_baseline_high": ("hrv", "baseline.balancedUpper", "int"),
    "resting_hr": ("summary", "heartRate.restingValue", "int"),
    "stress_avg": ("summary", "stress.avgLevel", "int"),
    "body_battery_high": ("summary", "bodyBattery.maxValue", "int"),
    "body_battery_low": ("summary", "bodyBattery.minValue", "int"),
    "body_battery_charged": ("summary", "bodyBattery.chargedValue", "int"),
    "body_battery_drained": ("summary", "bodyBattery.drainedValue", "int"),
    "steps": ("summary", "movement.steps.value", "int"),
    "active_calories": ("summary", "calories.burnedActive", "int"),
    "respiration_avg": ("summary", "respiration.avgValue", "float"),
    "vo2max": ("vo2max", "generic.vo2MaxPreciseValue", "float"),
}
CONVERT = {"int": lambda v: _int(v), "float": lambda v: _float(v), "ts": lambda v: _ts_local(v),
           "str": lambda v: str(v) if v is not None else None}


def build_health(day: str, parts: dict[str, dict]) -> dict | None:
    """One health_daily row from the per-endpoint records of a single day."""
    row = {"source": "garmin", "date": day}
    for col, (endpoint, path, kind) in HEALTH_MAP.items():
        row[col] = CONVERT[kind](dig(parts.get(endpoint) or {}, path))
    if row["sleep_seconds"] == 0:  # Garmin reports 0 when no sleep was recorded
        for col in ("sleep_start", "sleep_end", "sleep_seconds", "sleep_score"):
            row[col] = None
    if all(row[c] is None for c in HEALTH_MAP):
        return None
    row["raw_json"] = to_json({k: v for k, v in parts.items() if v})
    return row


class GarminSource(Source):
    name = "garmin"
    required_env = ("GARMIN_PERSONAL_API_KEY",)
    lookback_days = 3
    max_window_days = 28

    def __init__(self, env: dict[str, str], client: GarminClient | None = None) -> None:
        super().__init__(env)
        self.client = client or GarminClient(env["GARMIN_PERSONAL_API_KEY"])
        self.fit_limit = int(env.get("FIT_DOWNLOAD_LIMIT", "50"))

    def _activities(self, start: date, end: date) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self.client.get_json("/activitylist-service/activities/search/activities", {
                "start": offset, "limit": PAGE_SIZE,
                "startDate": start.isoformat(), "endDate": end.isoformat()})
            page = page if isinstance(page, list) else []
            out.extend(page)
            if len(page) < PAGE_SIZE:
                return out
            offset += PAGE_SIZE

    def _health(self, start: date, end: date) -> list[dict]:
        s, e = start.isoformat(), end.isoformat()
        c = self.client
        calls = {
            "sleep": lambda: by_date(c.get_json("/sleep-service/sleep/dailySleepSummariesByDate",
                                                {"startDate": s, "endDate": e})),
            "hrv": lambda: by_date((c.get_json(f"/hrv-service/hrv/daily/{s}/{e}") or {}).get("hrvSummaries")),
            "summary": lambda: by_date((c.get_json("/usersummary-service/v2/dailysummaries",
                                                   {"startDate": s, "endDate": e}) or {}).get("data")),
            "vo2max": lambda: by_date(c.get_json(f"/metrics-service/metrics/maxmet/daily/{s}/{e}"),
                                      "generic.calendarDate"),
        }
        by_endpoint: dict[str, dict[str, dict]] = {}
        for name, call in calls.items():
            try:
                by_endpoint[name] = call()
            except (GarminApiError, AttributeError, TypeError) as err:
                # One endpoint failing must not lose the others; the run is still marked partial.
                logger.error("garmin %s: %s", name, err)
                self.partial_errors.append(f"{name}: {err}")
                by_endpoint[name] = {}

        rows, d = [], start
        while d <= end:
            day = d.isoformat()
            row = build_health(day, {n: idx.get(day) for n, idx in by_endpoint.items()})
            if row:
                rows.append(row)
            d += timedelta(days=1)
        return rows

    def fetch(self, start: date, end: date, known_activity_ids: set[str]) -> Batch:
        self.partial_errors: list[str] = []
        raw_acts = self._activities(start, end)
        acts = [parse_activity(a) for a in raw_acts if a.get("activityId")]
        files: list[RawFile] = []
        for act in acts:
            if act["activity_id"] in known_activity_ids:
                continue
            if len(files) >= self.fit_limit:
                logger.info("garmin: лимит загрузки .FIT за прогон (%d) — остальные в следующий раз", self.fit_limit)
                break
            r = self.client.get(f"/download-service/files/activity/{act['activity_id']}",
                                accept="application/zip, application/x-zip-compressed, application/octet-stream")
            if r.ok and r.content:
                path = f"garmin/activities/{act['activity_id']}.zip"
                files.append(RawFile(path, r.content, "application/zip"))
                act["raw_file"] = path
                act.update(extract_zones(r.content))  # the watch's HR zones, empty if the file has none
            else:
                logger.warning("garmin: файл активности %s: HTTP %d", act["activity_id"], r.status_code)
        health = self._health(start, end)
        logger.info("garmin %s…%s: активностей %d, файлов %d, дней здоровья %d (запросов %d, повторов %d)",
                    start, end, len(acts), len(files), len(health), self.client.calls, self.client.retries)
        if self.partial_errors:
            raise PartialFetch(Batch(acts, health, files), self.partial_errors)
        return Batch(acts, health, files)


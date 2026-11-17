"""Garmin Connect via the official Personal API (personal API key).

Docs: https://developer.garmin.com/gc-developer-program/personal/personal-api/
  * key -> 24h bearer token (POST .../personal-api-key/token, "ApiKey <key>");
    no refresh tokens, just ask for a new one;
  * data: GET https://personal.connectapi.garmin.com/<path>, "Bearer <token>";
  * pull only. Rate limits are not documented: requests are paced and retried
    with exponential backoff on 429/5xx (Retry-After honoured).

Field names below follow Garmin's JSON. Endpoints whose response shape is not
documented are parsed defensively (by calendar date + candidate field names)
and always kept verbatim in raw_json.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import date, datetime, timedelta, timezone

import requests

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
def _flat(d: dict, prefix: str = "", out: dict | None = None) -> dict:
    """Flatten nested dicts; keys reachable both by full path and by leaf name (lower-case)."""
    out = {} if out is None else out
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            _flat(v, path, out)
        else:
            out.setdefault(path.lower(), v)
            out.setdefault(str(k).lower(), v)
    return out


def _pick(flat: dict, *names):
    for n in names:
        v = flat.get(n.lower())
        if v is not None:
            return v
    return None


DATE_KEYS = ("calendardate", "date", "summarydate")


def rows_by_date(payload) -> dict[str, dict]:
    """Find per-day records anywhere in a payload and merge them by calendar date."""
    found: dict[str, dict] = {}

    def walk(node):
        if isinstance(node, dict):
            flat = _flat(node)
            day = next((str(flat[k])[:10] for k in DATE_KEYS if isinstance(flat.get(k), str)), None)
            if day and len(day) == 10 and day[4] == "-":
                found.setdefault(day, {}).update({k: v for k, v in flat.items() if v is not None})
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return found


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
        "elevation_gain": _float(a.get("elevationGain")),
        "avg_heart_rate": _int(a.get("averageHR")),
        "max_heart_rate": _int(a.get("maxHR")),
        "avg_pace_sec_per_km": pace,
        "calories": _int(a.get("calories")),
        "aerobic_te": _float(a.get("aerobicTrainingEffect")),
        "anaerobic_te": _float(a.get("anaerobicTrainingEffect")),
        "training_load": _float(a.get("activityTrainingLoad")),
        "vo2max": _float(a.get("vO2MaxValue")),
        "raw_json": to_json(a),
    }


def parse_sleep(raw) -> dict:
    """/sleep-service/sleep/dailySleep -> sleep fields (shape: {dailySleepDTO: {...}, ...})."""
    if not isinstance(raw, dict) or not raw.get("dailySleepDTO"):
        return {}
    dto = raw["dailySleepDTO"]
    if not dto.get("sleepTimeSeconds"):
        return {}
    score = ((dto.get("sleepScores") or {}).get("overall") or {}).get("value")
    return {
        "sleep_start": _ts_local(dto.get("sleepStartTimestampLocal")),
        "sleep_end": _ts_local(dto.get("sleepEndTimestampLocal")),
        "sleep_seconds": _int(dto.get("sleepTimeSeconds")),
        "deep_sleep_seconds": _int(dto.get("deepSleepSeconds")),
        "light_sleep_seconds": _int(dto.get("lightSleepSeconds")),
        "rem_sleep_seconds": _int(dto.get("remSleepSeconds")),
        "awake_seconds": _int(dto.get("awakeSleepSeconds")),
        "sleep_score": _int(score),
        "spo2_avg": _float(raw.get("averageSpO2Value") or dto.get("averageSpO2Value")),
        "respiration_avg": _float(raw.get("averageRespirationValue") or dto.get("averageRespirationValue")),
    }


def body_battery_range(flat: dict) -> tuple[int | None, int | None]:
    """High/low from bodyBatteryValuesArray if explicit high/low fields are absent."""
    arr = flat.get("bodybatteryvaluesarray")
    if not isinstance(arr, list):
        return None, None
    idx = 1
    desc = flat.get("bodybatteryvaluedescriptordtolist")
    if isinstance(desc, list):
        for d in desc:
            if isinstance(d, dict) and "level" in str(d.get("bodyBatteryValueDescriptorKey", "")).lower():
                idx = d.get("bodyBatteryValueDescriptorIndex", idx)
    vals = [p[idx] for p in arr if isinstance(p, list) and len(p) > idx and isinstance(p[idx], (int, float))]
    return (max(vals), min(vals)) if vals else (None, None)


HEALTH_FIELDS = {
    # column: candidate Garmin field names (leaf, case-insensitive), first hit wins
    "hrv_last_night": ("lastNightAvg",),
    "hrv_weekly_avg": ("weeklyAvg",),
    "hrv_status": ("status",),
    "resting_hr": ("restingHeartRate", "restingHR", "values.restingHR"),
    "stress_avg": ("overallStressLevel", "averageStressLevel", "avgStressLevel"),
    "body_battery_high": ("bodyBatteryHighestValue", "highestValue", "maxBodyBattery"),
    "body_battery_low": ("bodyBatteryLowestValue", "lowestValue", "minBodyBattery"),
    "body_battery_charged": ("charged", "bodyBatteryChargedValue"),
    "body_battery_drained": ("drained", "bodyBatteryDrainedValue"),
    "steps": ("totalSteps",),
    "active_calories": ("activeKilocalories",),
    "vo2max": ("vo2MaxPreciseValue", "vo2MaxValue"),
}
INT_FIELDS = {"resting_hr", "stress_avg", "body_battery_high", "body_battery_low",
              "body_battery_charged", "body_battery_drained", "steps", "active_calories",
              "hrv_last_night", "hrv_weekly_avg"}


def build_health(day: str, parts: dict[str, dict], sleep_raw) -> dict | None:
    """Merge per-endpoint per-day records into one health_daily row."""
    row = {"source": "garmin", "date": day}
    merged: dict = {}
    for name in ("hrv", "stress", "body_battery", "daily_summary", "vo2max"):
        merged.update({k: v for k, v in (parts.get(name) or {}).items() if k not in merged})
    for col, names in HEALTH_FIELDS.items():
        v = _pick(merged, *names)
        row[col] = _int(v) if col in INT_FIELDS else (_float(v) if col == "vo2max" else v)
    if row["body_battery_high"] is None:
        hi, lo = body_battery_range(parts.get("body_battery") or {})
        row["body_battery_high"], row["body_battery_low"] = _int(hi), _int(lo)
    row.update(parse_sleep(sleep_raw))
    payload = {k: v for k, v in parts.items() if v}
    if sleep_raw:
        payload["sleep"] = sleep_raw
    if len(row) <= 2 or all(v is None for k, v in row.items() if k not in ("source", "date")):
        return None
    row["raw_json"] = to_json(payload)
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
        sources = {
            "hrv": lambda: c.get_json(f"/hrv-service/hrv/daily/{s}/{e}"),
            "stress": lambda: c.get_json(f"/usersummary-service/stats/stress/daily/{s}/{e}"),
            "body_battery": lambda: c.get_json("/wellness-service/wellness/bodyBattery/reports/daily",
                                               {"startDate": s, "endDate": e}),
            "daily_summary": lambda: c.get_json("/usersummary-service/v2/dailysummaries",
                                                {"startDate": s, "endDate": e}),
            "vo2max": lambda: c.get_json(f"/metrics-service/metrics/maxmet/daily/{s}/{e}"),
        }
        by_endpoint: dict[str, dict[str, dict]] = {}
        for name, call in sources.items():
            try:
                by_endpoint[name] = rows_by_date(call())
            except GarminApiError as err:
                # One health endpoint failing must not lose the others; surfaced as a failed run.
                logger.error("garmin %s: %s", name, err)
                self.partial_errors.append(f"{name}: {err}")
                by_endpoint[name] = {}

        rows, d = [], start
        while d <= end:
            day = d.isoformat()
            try:
                sleep_raw = c.get_json("/sleep-service/sleep/dailySleep", {"date": day})
            except GarminApiError as err:
                logger.error("garmin sleep %s: %s", day, err)
                self.partial_errors.append(f"sleep {day}: {err}")
                sleep_raw = None
            row = build_health(day, {n: by_endpoint[n].get(day) or {} for n in by_endpoint}, sleep_raw)
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
            else:
                logger.warning("garmin: файл активности %s: HTTP %d", act["activity_id"], r.status_code)
        health = self._health(start, end)
        logger.info("garmin %s…%s: активностей %d, файлов %d, дней здоровья %d (запросов %d, повторов %d)",
                    start, end, len(acts), len(files), len(health), self.client.calls, self.client.retries)
        if self.partial_errors:
            raise PartialFetch(Batch(acts, health, files), self.partial_errors)
        return Batch(acts, health, files)


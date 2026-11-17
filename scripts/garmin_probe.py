#!/usr/bin/env python3
"""Probe the official Garmin Connect Personal API (read-only).

Checks what the API actually returns for your account:
  * activity list for the last 7 days and for older windows (history depth);
  * one original activity file (ZIP -> .FIT) and whether it carries VO2 max,
    training effect and recovery-time data;
  * one day of health data (sleep, HRV, stress, Body Battery, resting HR, ...);
  * rate-limit headers and status codes seen along the way.

Nothing is written to BigQuery or GCP. Raw responses go to --out (default
./garmin_probe_out, git-ignored) so they can be inspected later.

Usage:
  export GARMIN_PERSONAL_API_KEY=...        # never paste it into chat or commit it
  pip install requests garmin-fit-sdk
  python scripts/garmin_probe.py [--date YYYY-MM-DD] [--out DIR]
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests

BASE_URL = os.environ.get("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
PAUSE_SECONDS = 1.0  # be polite: one request per second

# Metric -> substrings of JSON field names that indicate it is present.
HEALTH_METRICS = {
    "Сон: длительность":        ["sleeptimeseconds"],
    "Сон: фазы (deep/REM)":     ["deepsleepseconds", "remsleepseconds"],
    "Сон: score":               ["sleepscores"],
    "HRV":                      ["lastnightavg", "weeklyavg"],
    "Stress":                   ["averagestresslevel", "overallstresslevel"],
    "Body Battery":             ["bodybattery", "charged"],
    "Пульс покоя":              ["restingheartrate"],
    "SpO2":                     ["spo2"],
    "Дыхание":                  ["respiration"],
    "Шаги":                     ["totalsteps"],
    "VO2 max (health API)":     ["vo2max"],
    "Training readiness":       ["trainingreadiness", "readiness"],
    "Training status / нагрузка": ["trainingstatus", "acuteload", "trainingload"],
}


class Probe:
    def __init__(self, api_key: str, out: Path) -> None:
        self.s = requests.Session()
        self.out = out
        self.calls: list[dict] = []
        self.rate_headers: dict[str, str] = {}
        self.token = self._get_token(api_key)

    # ---- HTTP -------------------------------------------------------------
    def _note(self, method: str, path: str, resp: requests.Response, t0: float) -> None:
        self.calls.append({"method": method, "path": path.split("?")[0],
                           "status": resp.status_code, "ms": int((time.time() - t0) * 1000)})
        for k, v in resp.headers.items():
            lk = k.lower()
            if "ratelimit" in lk or "rate-limit" in lk or lk in ("retry-after", "x-retry-after"):
                self.rate_headers[k] = v

    def _get_token(self, api_key: str) -> str:
        t0 = time.time()
        r = self.s.post(BASE_URL + TOKEN_PATH,
                        headers={"Accept": "*/*", "Authorization": "ApiKey " + api_key}, timeout=30)
        self._note("POST", TOKEN_PATH, r, t0)
        if not r.ok:
            sys.exit(f"Не удалось получить токен: HTTP {r.status_code}. Проверьте ключ и срок его действия.")
        return r.text.strip()  # JWT, valid 24h; never printed

    def get(self, path: str, params: dict | None = None, accept: str = "application/json"):
        time.sleep(PAUSE_SECONDS)
        t0 = time.time()
        r = self.s.get(BASE_URL + path, params=params, timeout=60,
                       headers={"Accept": accept, "Authorization": "Bearer " + self.token})
        self._note("GET", path, r, t0)
        if r.status_code == 429:
            print(f"  ! 429 Too Many Requests на {path}; Retry-After={r.headers.get('Retry-After')}")
        return r

    def get_json(self, name: str, path: str, params: dict | None = None):
        r = self.get(path, params)
        body = None
        if r.ok and r.content:
            try:
                body = r.json()
            except ValueError:
                body = None
        (self.out / f"{name}.json").write_text(
            json.dumps({"status": r.status_code, "path": path, "params": params, "body": body},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return r.status_code, body


# ---- helpers ---------------------------------------------------------------
def flat_keys(obj, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if v not in (None, [], {}, ""):
                keys.add(p)
            keys |= flat_keys(v, p)
    elif isinstance(obj, list):
        for item in obj[:5]:
            keys |= flat_keys(item, prefix + "[]")
    return keys


def list_activities(p: Probe, label: str, start: date, end: date) -> list:
    status, body = p.get_json(f"activities_{label}",
                              "/activitylist-service/activities/search/activities",
                              {"start": 0, "limit": 100,
                               "startDate": start.isoformat(), "endDate": end.isoformat()})
    acts = body if isinstance(body, list) else []
    print(f"  {label:<14} {start} … {end}: HTTP {status}, активностей: {len(acts)}")
    return acts


def inspect_fit(p: Probe, activity_id) -> dict:
    r = p.get(f"/download-service/files/activity/{activity_id}",
              accept="application/zip, application/x-zip-compressed, application/octet-stream")
    if not r.ok or not r.content:
        return {"error": f"HTTP {r.status_code}"}
    (p.out / f"activity_{activity_id}.zip").write_bytes(r.content)
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names = z.namelist()
            fit_name = next((n for n in names if n.lower().endswith(".fit")), None)
            if not fit_name:
                return {"error": "в ZIP нет .fit", "zip_files": names}
            fit_bytes = z.read(fit_name)
    except zipfile.BadZipFile:
        fit_bytes, names, fit_name = r.content, [], "(raw)"

    try:
        from garmin_fit_sdk import Decoder, Stream
    except ImportError:
        return {"error": "не установлен garmin-fit-sdk (pip install garmin-fit-sdk)"}

    decoder = Decoder(Stream.from_byte_array(bytearray(fit_bytes)))
    # Unknown (undocumented) messages are kept by the SDK under their number, e.g. "140".
    messages, errors = decoder.read(convert_types_to_strings=True)
    session = (messages.get("session_mesgs") or [{}])[0]
    max_met = messages.get("max_met_data_mesgs") or []
    unknown_140 = messages.get("140") or messages.get(140) or []

    def first_vo2():
        for m in max_met:
            for k in ("vo2_max", "vo2max"):
                if m.get(k) is not None:
                    return m.get(k)
        return None

    return {
        "fit_file": fit_name,
        "zip_files": names,
        "message_types": sorted(str(k) for k in messages.keys()),
        "decode_errors": [str(e) for e in errors],
        "VO2 max (max_met_data)": first_vo2(),
        "Aerobic training effect": session.get("total_training_effect"),
        "Anaerobic training effect": session.get("total_anaerobic_training_effect"),
        # Recovery time / performance condition live in undocumented message 140.
        "Сообщение 140 (recovery/perf. condition, недокументировано)": bool(unknown_140),
        "msg140_fields": sorted(str(k) for k in (unknown_140[0].keys() if unknown_140 else [])),
    }


# ---- main ------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="день для health-сводки (по умолчанию вчера)")
    ap.add_argument("--out", default="garmin_probe_out")
    args = ap.parse_args()

    api_key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
    if not api_key:
        sys.exit("Задайте переменную окружения GARMIN_PERSONAL_API_KEY.")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)  # personal health data

    today = date.today()
    day = date.fromisoformat(args.date) if args.date else today - timedelta(days=1)
    p = Probe(api_key, out)
    del api_key
    print("Токен получен.\n")

    # 1. Activities and history depth
    print("1) Активности и глубина истории")
    windows = {"7d": (today - timedelta(days=7), today)}
    for months in (13, 25, 37, 61):
        end = today - timedelta(days=30 * months)
        windows[f"{months}mo_ago"] = (end - timedelta(days=30), end)
    found = {label: list_activities(p, label, s, e) for label, (s, e) in windows.items()}
    oldest_ok = [lbl for lbl, acts in found.items() if acts]

    # 2. One .FIT file
    print("\n2) Файл активности (.FIT)")
    sample = (found["7d"] or next((a for a in found.values() if a), []))
    fit_info = {"error": "нет активностей для проверки"}
    if sample:
        aid = sample[0].get("activityId")
        print(f"  activityId {aid} ({sample[0].get('activityType', {}).get('typeKey')})")
        fit_info = inspect_fit(p, aid)
        (out / "fit_summary.json").write_text(json.dumps(fit_info, ensure_ascii=False, indent=2, default=str))
        for k, v in fit_info.items():
            if k not in ("message_types", "msg140_fields", "zip_files"):
                print(f"  {k}: {v}")
        print(f"  типы сообщений: {', '.join(fit_info.get('message_types', []))}")

    # 3. Health for one day
    print(f"\n3) Health за {day}")
    d = day.isoformat()
    endpoints = {
        "sleep":           ("/sleep-service/sleep/dailySleep", {"date": d}),
        "hrv":             (f"/hrv-service/hrv/{d}", None),
        "stress":          (f"/usersummary-service/stats/stress/daily/{d}/{d}", None),
        "body_battery":    ("/wellness-service/wellness/bodyBattery/reports/daily", {"startDate": d, "endDate": d}),
        "daily_summary":   ("/usersummary-service/v2/dailysummaries", {"startDate": d, "endDate": d}),
        "daily_hr":        ("/wellness-service/wellness/dailyHeartRate", {"date": d}),
        "vo2max":          (f"/metrics-service/metrics/maxmet/daily/{d}/{d}", None),
        "spo2":            (f"/wellness-service/wellness/daily/spo2acclimation/{d}", None),
        # Not in Garmin's documented wrapper list; 404/403 here is expected and informative.
        "training_readiness_undoc": (f"/metrics-service/metrics/trainingreadiness/{d}", None),
        "training_status_undoc":    (f"/metrics-service/metrics/trainingstatus/aggregated/{d}", None),
    }
    health_keys: dict[str, set[str]] = {}
    for name, (path, params) in endpoints.items():
        status, body = p.get_json(f"health_{name}", path, params)
        keys = flat_keys(body)
        health_keys[name] = keys
        print(f"  {name:<26} HTTP {status:<4} полей: {len(keys)}")

    # Health history depth: same daily summary 13 months back.
    old = (today - timedelta(days=395)).isoformat()
    st_old, body_old = p.get_json("health_daily_summary_13mo", "/usersummary-service/v2/dailysummaries",
                                  {"startDate": old, "endDate": old})
    health_history = bool(flat_keys(body_old)) and st_old == 200

    # ---- Report --------------------------------------------------------------
    all_keys = {k.lower(): src for src, ks in health_keys.items() for k in ks}
    rows = []
    for metric, needles in HEALTH_METRICS.items():
        hits = sorted({src for k, src in all_keys.items() if any(n in k.rsplit(".", 1)[-1].replace("_", "") for n in needles)})
        rows.append((metric, "есть" if hits else "нет", ", ".join(hits)))
    for metric in ("VO2 max (max_met_data)", "Aerobic training effect", "Anaerobic training effect",
                   "Сообщение 140 (recovery/perf. condition, недокументировано)"):
        v = fit_info.get(metric)
        present = v is not None and v is not False  # 0.0 is a real value (e.g. strength training TE)
        rows.append((f".FIT: {metric}", "есть" if present else "нет", "" if v is None or isinstance(v, bool) else str(v)))

    print("\n================ ИТОГ ================")
    w = max(len(r[0]) for r in rows)
    for m, yn, src in rows:
        print(f"{m:<{w}}  {yn:<4}  {src}")
    print(f"\nИстория активностей: есть данные в окнах {oldest_ok or 'нет'}")
    print(f"История health 13 мес. назад ({old}): {'есть' if health_history else 'нет'}")
    codes: dict[int, int] = {}
    for c in p.calls:
        codes[c["status"]] = codes.get(c["status"], 0) + 1
    print(f"Запросов: {len(p.calls)}, коды ответов: {codes}")
    print(f"Заголовки лимитов: {p.rate_headers or 'не прислано'}")
    (out / "report.json").write_text(json.dumps({
        "rows": rows, "activity_windows_with_data": oldest_ok, "health_history_13mo": health_history,
        "calls": p.calls, "rate_headers": p.rate_headers, "fit": fit_info,
    }, ensure_ascii=False, indent=2, default=str))
    print(f"Сырые ответы и report.json: {out.resolve()}")


if __name__ == "__main__":
    main()

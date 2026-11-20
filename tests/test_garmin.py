from datetime import date

import pytest
import requests

from secondwind.apps.collector.sources.base import PartialFetch
from secondwind.apps.collector.sources.garmin import GarminApiError, GarminClient, GarminSource, build_health, by_date, dig, parse_activity


class Resp:
    def __init__(self, status=200, body=None, content=None, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text
        self.ok = status < 400
        self._body = body
        self.content = content if content is not None else (b"x" if body is not None else b"")

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, routes):
        self.routes, self.log = routes, []

    def request(self, method, url, **kw):
        self.log.append((method, url, kw.get("headers", {}).get("Authorization", "")[:6]))
        for key, handler in self.routes.items():
            if key in url:
                r = handler(method, url, kw)
                if isinstance(r, Exception):
                    raise r
                return r
        return Resp(404)


def client(routes):
    return GarminClient("KEY", session=FakeSession(routes), sleep=lambda s: None)


def token_route():
    return {"/token": lambda m, u, k: Resp(200, text="jwt-token")}


ACT = {
    "activityId": 123, "activityName": "Morning Run", "startTimeLocal": "2026-11-16 07:05:00",
    "startTimeGMT": "2026-11-16 04:05:00", "activityType": {"typeKey": "running"},
    "distance": 10000.0, "duration": 3100.0, "movingDuration": 3000.0, "elevationGain": 55.0,
    "averageHR": 150.4, "maxHR": 171, "calories": 700, "aerobicTrainingEffect": 3.4,
    "anaerobicTrainingEffect": 1.2, "activityTrainingLoad": 120.5, "vO2MaxValue": 52,
    "trainingEffectLabel": "TEMPO",
}


def test_parse_activity_uses_utc_start_local_date_and_moving_time():
    row = parse_activity(ACT)
    assert row["start_time"] == "2026-11-16T04:05:00+00:00"
    assert row["activity_date"] == "2026-11-16"
    assert row["duration_seconds"] == 3000 and row["elapsed_seconds"] == 3100
    assert row["avg_pace_sec_per_km"] == 300.0
    assert row["avg_heart_rate"] == 150
    assert (row["aerobic_te"], row["training_load"], row["vo2max"]) == (3.4, 120.5, 52.0)
    assert row["source"] == "garmin" and row["activity_id"] == "123"
    assert row["training_effect_label"] == "TEMPO"


def test_pace_only_for_running():
    assert parse_activity({**ACT, "activityType": {"typeKey": "cycling"}})["avg_pace_sec_per_km"] is None


# Shapes copied from real Personal API responses (values are synthetic).
SLEEP = [{"calendarDate": "2026-11-16", "sleepTimeSeconds": 27000, "deepSleepSeconds": 5400,
          "lightSleepSeconds": 14000, "remSleepSeconds": 6000, "awakeSleepSeconds": 1600,
          "sleepStartTimestampLocal": 1763247000000, "sleepEndTimestampLocal": 1763274000000,
          "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}}}]
HRV = {"hrvSummaries": [{"calendarDate": "2026-11-16", "weeklyAvg": 55, "lastNightAvg": 50, "status": "BALANCED",
                         "lastNight5MinHigh": 70, "baseline": {"balancedLow": 48, "balancedUpper": 62}}],
       "userProfilePk": 1}
SUMMARY = {"data": [{"calendarDate": "2026-11-16", "movement": {"steps": {"value": 9000, "goal": 8000}},
                     "calories": {"burnedActive": 650}, "heartRate": {"restingValue": 47},
                     "stress": {"avgLevel": 30}, "respiration": {"avgValue": 14},
                     "bodyBattery": {"minValue": 20, "maxValue": 85, "chargedValue": 60, "drainedValue": 55}}]}
VO2 = [{"generic": {"calendarDate": "2026-11-16", "vo2MaxPreciseValue": 51.7, "vo2MaxValue": 52.0}, "cycling": None}]


def test_dig_and_by_date():
    assert dig(SUMMARY["data"][0], "heartRate.restingValue") == 47
    assert dig({}, "a.b") is None
    assert set(by_date(VO2, "generic.calendarDate")) == {"2026-11-16"}


def test_build_health_from_real_shapes():
    parts = {"sleep": SLEEP[0], "hrv": HRV["hrvSummaries"][0], "summary": SUMMARY["data"][0], "vo2max": VO2[0]}
    row = build_health("2026-11-16", parts)
    assert row["sleep_seconds"] == 27000 and row["sleep_score"] == 82 and row["deep_sleep_seconds"] == 5400
    assert row["sleep_start"] == "2025-11-15T22:50:00"  # local wall time from Garmin's local epoch
    assert (row["hrv_last_night"], row["hrv_weekly_avg"], row["hrv_status"]) == (50, 55, "BALANCED")
    assert (row["hrv_5min_high"], row["hrv_baseline_low"], row["hrv_baseline_high"]) == (70, 48, 62)
    assert (row["resting_hr"], row["stress_avg"], row["steps"], row["active_calories"]) == (47, 30, 9000, 650)
    assert (row["body_battery_low"], row["body_battery_high"]) == (20, 85)
    assert row["respiration_avg"] == 14.0 and row["vo2max"] == 51.7
    assert '"summary"' in row["raw_json"]


def test_build_health_zero_sleep_is_no_sleep():
    row = build_health("2026-11-16", {"sleep": {**SLEEP[0], "sleepTimeSeconds": 0, "sleepScores": {}},
                                      "summary": SUMMARY["data"][0]})
    assert row["sleep_seconds"] is None and row["steps"] == 9000


def test_build_health_empty_day_is_skipped():
    assert build_health("2026-11-16", {"hrv": None, "summary": {}}) is None


def test_client_refreshes_token_on_401_and_retries_429():
    calls = {"n": 0}

    def data(m, u, k):
        calls["n"] += 1
        if calls["n"] == 1:
            return Resp(401)
        if calls["n"] == 2:
            return Resp(429, headers={"Retry-After": "2"})
        return Resp(200, body=[{"ok": 1}])

    c = client({**token_route(), "/activitylist": data})
    assert c.get_json("/activitylist-service/x") == [{"ok": 1}]
    tokens = [x for x in c.s.log if x[1].endswith("/token")]
    assert len(tokens) == 2  # initial + after 401
    assert c.retries == 1


def test_client_gives_up_after_max_attempts():
    c = client({**token_route(), "/x": lambda m, u, k: requests.ConnectionError("boom")})
    with pytest.raises(GarminApiError):
        c.get_json("/x")


def test_rejected_key_is_reported_clearly():
    c = client({"/token": lambda m, u, k: Resp(401)})
    with pytest.raises(GarminApiError, match="API key"):
        c.get_json("/x")


def _source(routes, **env):
    return GarminSource({"GARMIN_PERSONAL_API_KEY": "KEY", **env}, client=client({**token_route(), **routes}))


def test_fetch_downloads_fit_only_for_new_activities_and_respects_limit():
    acts = [{**ACT, "activityId": i} for i in (1, 2, 3)]
    routes = {
        "/activitylist": lambda m, u, k: Resp(200, body=acts),
        "/download-service": lambda m, u, k: Resp(200, content=b"PK.."),
    }
    src = _source(routes, FIT_DOWNLOAD_LIMIT="1")
    batch = src.fetch(date(2026, 11, 16), date(2026, 11, 16), known_activity_ids={"1"})
    assert len(batch.activities) == 3
    assert [f.path for f in batch.files] == ["garmin/activities/2.zip"]
    assert {a["activity_id"]: a.get("raw_file") for a in batch.activities} == {
        "1": None, "2": "garmin/activities/2.zip", "3": None}


def test_activity_pagination():
    pages = {0: [{**ACT, "activityId": i} for i in range(100)], 100: [{**ACT, "activityId": 999}]}

    def lst(m, u, k):
        return Resp(200, body=pages[k["params"]["start"]])

    src = _source({"/activitylist": lst, "/download-service": lambda m, u, k: Resp(404)})
    assert len(src._activities(date(2026, 1, 1), date(2026, 1, 28))) == 101


def test_health_uses_four_range_calls():
    routes = {
        "/activitylist": lambda m, u, k: Resp(200, body=[]),
        "/dailySleepSummariesByDate": lambda m, u, k: Resp(200, body=SLEEP),
        "/hrv-service/hrv/daily/": lambda m, u, k: Resp(200, body=HRV),
        "/dailysummaries": lambda m, u, k: Resp(200, body=SUMMARY),
        "/maxmet/daily/": lambda m, u, k: Resp(200, body=VO2),
    }
    src = _source(routes)
    batch = src.fetch(date(2026, 11, 15), date(2026, 11, 16), set())
    assert [r["date"] for r in batch.health_daily] == ["2026-11-16"]
    data_calls = [u for _, u, _ in src.client.s.log if "/token" not in u]
    assert len(data_calls) == 5  # activity list + 4 health ranges, independent of window length


def test_health_endpoint_failure_is_partial_not_silent():
    routes = {
        "/activitylist": lambda m, u, k: Resp(200, body=[]),
        "/hrv-service": lambda m, u, k: Resp(500),
        "/dailySleepSummariesByDate": lambda m, u, k: Resp(200, body=SLEEP),
    }
    src = _source(routes)
    with pytest.raises(PartialFetch) as e:
        src.fetch(date(2026, 11, 16), date(2026, 11, 16), set())
    assert e.value.batch.health_daily[0]["sleep_seconds"] == 27000

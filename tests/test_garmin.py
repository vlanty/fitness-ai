from datetime import date

import pytest
import requests

from sources.base import PartialFetch
from sources.garmin import (GarminApiError, GarminClient, GarminSource, body_battery_range, build_health,
                            parse_activity, rows_by_date)


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
}


def test_parse_activity_uses_utc_start_local_date_and_moving_time():
    row = parse_activity(ACT)
    assert row["start_time"] == "2026-11-16T04:05:00+00:00"
    assert row["activity_date"] == "2026-11-16"
    assert row["duration_seconds"] == 3000
    assert row["avg_pace_sec_per_km"] == 300.0
    assert row["avg_heart_rate"] == 150
    assert (row["aerobic_te"], row["training_load"], row["vo2max"]) == (3.4, 120.5, 52.0)
    assert row["source"] == "garmin" and row["activity_id"] == "123"


def test_pace_only_for_running():
    assert parse_activity({**ACT, "activityType": {"typeKey": "cycling"}})["avg_pace_sec_per_km"] is None


def test_rows_by_date_finds_nested_daily_records():
    payload = {"hrvSummaries": [{"calendarDate": "2026-11-15", "lastNightAvg": 50, "weeklyAvg": 55, "status": "BALANCED"},
                                {"calendarDate": "2026-11-16", "lastNightAvg": 48}]}
    rows = rows_by_date(payload)
    assert rows["2026-11-15"]["lastnightavg"] == 50
    assert rows["2026-11-16"]["lastnightavg"] == 48

    stress = [{"calendarDate": "2026-11-16", "values": {"overallStressLevel": 31}}]
    assert rows_by_date(stress)["2026-11-16"]["overallstresslevel"] == 31


def test_body_battery_from_values_array():
    flat = rows_by_date([{"date": "2026-11-16", "charged": 60, "drained": 55,
                          "bodyBatteryValuesArray": [[1, 20], [2, 85], [3, 40]]}])["2026-11-16"]
    assert body_battery_range(flat) == (85, 20)


def test_build_health_merges_endpoints_and_sleep():
    parts = {
        "hrv": {"lastnightavg": 50, "weeklyavg": 55, "status": "BALANCED"},
        "daily_summary": {"restingheartrate": 47, "totalsteps": 9000},
        "body_battery": rows_by_date([{"date": "2026-11-16", "bodyBatteryValuesArray": [[1, 20], [2, 85]]}])["2026-11-16"],
        "stress": {"overallstresslevel": 30},
        "vo2max": {}, 
    }
    sleep = {"dailySleepDTO": {"sleepTimeSeconds": 27000, "deepSleepSeconds": 5400, "remSleepSeconds": 6000,
                               "sleepStartTimestampLocal": 1763331000000, "sleepScores": {"overall": {"value": 82}}}}
    row = build_health("2026-11-16", parts, sleep)
    assert row["hrv_last_night"] == 50 and row["hrv_status"] == "BALANCED"
    assert row["resting_hr"] == 47 and row["steps"] == 9000 and row["stress_avg"] == 30
    assert (row["body_battery_high"], row["body_battery_low"]) == (85, 20)
    assert row["sleep_seconds"] == 27000 and row["sleep_score"] == 82
    assert row["sleep_start"].startswith("2025-11-16T") or row["sleep_start"].startswith("2025-11-17T")
    assert '"sleep"' in row["raw_json"]


def test_build_health_empty_day_is_skipped():
    assert build_health("2026-11-16", {"hrv": {}, "stress": {}}, None) is None


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
    with pytest.raises(GarminApiError, match="ключ"):
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


def test_health_endpoint_failure_is_partial_not_silent():
    routes = {
        "/activitylist": lambda m, u, k: Resp(200, body=[]),
        "/hrv-service": lambda m, u, k: Resp(500),
        "/dailySleep": lambda m, u, k: Resp(200, body={"dailySleepDTO": {"sleepTimeSeconds": 25000}}),
    }
    src = _source(routes)
    with pytest.raises(PartialFetch) as e:
        src.fetch(date(2026, 11, 16), date(2026, 11, 16), set())
    assert e.value.batch.health_daily[0]["sleep_seconds"] == 25000

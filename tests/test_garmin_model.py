"""Garmin payloads -> the universal model: what is mapped, how, and what is left out."""

import json

from secondwind.adapters.blob.raw_store import RawStore
from secondwind.apps.collector.sources.base import Batch, RawFile
from secondwind.apps.collector.sources.garmin import GarminSource
from secondwind.apps.collector.sources.garmin_model import activity_to_model, health_to_model
from secondwind.core.observations import observation
from secondwind.core.registry import registry

ACTIVITY = {
    "activityId": 123, "activityName": "Morning run", "activityType": {"typeKey": "running"},
    "startTimeGMT": "2026-11-19 06:00:00", "startTimeLocal": "2026-11-19 07:00:00",
    "distance": 10000.0, "movingDuration": 3000.0, "elapsedDuration": 3100.0, "duration": 3100.0,
    "elevationGain": 55.0, "averageHR": 150.0, "maxHR": 172.0, "calories": 700.0,
    "aerobicTrainingEffect": 3.4, "anaerobicTrainingEffect": 0.8, "trainingEffectLabel": "TEMPO",
    "activityTrainingLoad": 120.5, "vO2MaxValue": 52.0,
    "fastestSplit_1000": 270.0, "fastestSplit_5000": 1420.0, "hrTimeInZone_2": 600.0, "hrTimeInZone_3": 1800.0,
    "avgPower": 280.0, "normPower": 290.0, "isFavorite": False,
    "ownerFullName": "Real Name", "ownerId": 42, "startLatitude": 52.5, "startLongitude": 13.4,
}
HEALTH = {
    "sleep": {"sleepTimeSeconds": 27000, "deepSleepSeconds": 5400, "lightSleepSeconds": 14000,
              "remSleepSeconds": 6000, "awakeSleepSeconds": 1600, "sleepScores": {"overall": {"value": 82}},
              "sleepStartTimestampGMT": 1795476600000, "sleepEndTimestampGMT": 1795504200000,
              "sleepStartTimestampLocal": 1795480200000, "id": 99},
    "hrv": {"lastNightAvg": 50, "weeklyAvg": 52, "lastNight5MinHigh": 70, "status": "BALANCED",
            "baseline": {"balancedLow": 45, "balancedUpper": 60}},
    "summary": {"heartRate": {"restingValue": 47}, "stress": {"avgLevel": 25}, "movement": {"steps": {"value": 9000}},
                "bodyBattery": {"maxValue": 85, "minValue": 20}, "calories": {"burnedActive": 600},
                "respiration": {"avgValue": 14.5}},
    "vo2max": {"generic": {"vo2MaxPreciseValue": 52.3}, "cycling": {"vo2MaxPreciseValue": 49.1}},
}


def by_metric(model):
    return {o["metric"] + (f"#{o['vendor_field']}" if o["metric"].startswith("workout.best") or
                           o["metric"].startswith("workout.time_in") else ""): o for o in model.observations}


def test_activity_session_and_metrics():
    m = activity_to_model("u1", ACTIVITY, raw_ref="gs://b/raw.json", fit_ref="gs://b/a.zip")
    (s,) = m.sessions
    assert s["session_id"] == "garmin:workout:123" and s["sport"] == "run" and s["tz_offset_min"] == 60
    assert s["end_ts"].startswith("2026-11-19T06:51:40") and s["attrs"] == {"fit_ref": "gs://b/a.zip"}
    o = by_metric(m)
    assert o["workout.distance"]["value_num"] == 10000 and o["workout.load"]["method"] == "garmin_epoc_load"
    assert o["workout.pace.mean"]["value_num"] == 300 and o["vendor_score.garmin.te_label"]["value_text"] == "TEMPO"
    assert o["workout.best_effort_time#fastestSplit_5000"]["attrs"] == {"distance_m": 5000}
    assert o["workout.time_in_hr_zone#hrTimeInZone_3"]["attrs"] == {"zone": 3}
    assert all(x["session_id"] == "garmin:workout:123" and x["user_id"] == "u1" for x in m.observations)


def test_unknown_numeric_fields_are_kept_but_personal_ones_are_not():
    metrics = {o["metric"] for o in activity_to_model("u1", ACTIVITY).observations}
    assert {"x.garmin.activity.avgPower", "x.garmin.activity.normPower"} <= metrics
    assert not any(k in m for m in metrics for k in ("owner", "Latitude", "Longitude", "isFavorite"))


def test_health_day_with_groups_and_sleep_session():
    m = health_to_model("u1", "2026-11-19", HEALTH, raw_ref="gs://b/h.json")
    o = by_metric(m)
    assert o["hrv.night"]["value_num"] == 50 and o["hrv.night"]["method"] == "rmssd_sleep_night_mean"
    assert o["hrv.baseline_low"]["method"] == "garmin_hrv_baseline"
    assert o["vendor_score.garmin.hrv_status"]["value_text"] == "BALANCED"
    assert o["heart.resting"]["value_num"] == 47 and o["fitness.vo2max_bike"]["value_num"] == 49.1
    assert all(x["session_id"] is None if "session_id" in x else True for x in m.observations)
    (s,) = m.sessions
    assert s["kind"] == "sleep" and s["tz_offset_min"] == 60 and s["is_main"]


def test_no_sleep_means_no_sleep_metrics():
    parts = {**HEALTH, "sleep": {"sleepTimeSeconds": 0, "deepSleepSeconds": 0}}
    m = health_to_model("u1", "2026-11-19", parts)
    assert not m.sessions and not any(o["metric"].startswith("sleep.") for o in m.observations)


def test_reloading_gives_the_same_ids():
    a = [o["observation_id"] for o in health_to_model("u1", "2026-11-19", HEALTH).observations]
    b = [o["observation_id"] for o in health_to_model("u1", "2026-11-19", HEALTH).observations]
    assert a == b and len(set(a)) == len(a)


def test_unit_conversion_on_ingest():
    o = observation(user_id="u", source="s", metric="workout.distance", method="session_summary",
                    value=5, unit="km", local_date="2026-11-19", reg=registry())
    assert o["value_num"] == 5000 and o["unit"] == "m" and o["value_orig"] == "5" and o["unit_orig"] == "km"


class Bucket:
    name = "raw"

    def __init__(self):
        self.objects = {}

    def blob(self, path):
        objs = self.objects
        class B:
            def upload_from_string(self, content, content_type=None):
                objs[path] = content
        return B()


def test_to_model_stores_raw_payloads_in_the_users_folder():
    bucket = Bucket()
    src = GarminSource({"GARMIN_PERSONAL_API_KEY": "k"}, client=object())
    batch = Batch(activities=[{"activity_id": "123", "activity_date": "2026-11-19", "raw_file": "garmin/activities/123.zip",
                               "raw_json": json.dumps(ACTIVITY)}],
                  health_daily=[{"date": "2026-11-19", "raw_json": json.dumps(HEALTH)}],
                  files=[RawFile("garmin/activities/123.zip", b"not a fit file", "application/zip")])
    m = src.to_model("u1", batch, RawStore(bucket, "u1", "garmin", "run1"))
    assert sorted(bucket.objects) == ["users/u1/garmin/activities/123.zip", "users/u1/garmin/raw/activities/123.json",
                                      "users/u1/garmin/raw/health/2026-11-19.json"]
    assert {r["endpoint"] for r in m.raw_payloads} == {"activities", "fit", "health"}
    assert m.sessions[0]["raw_ref"] == "gs://raw/users/u1/garmin/raw/activities/123.json"
    assert m.sessions[0]["attrs"]["fit_ref"] == "gs://raw/users/u1/garmin/activities/123.zip"

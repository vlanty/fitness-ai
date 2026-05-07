import json
import logging
from datetime import date
from garminconnect import Garmin

logger = logging.getLogger(__name__)


def get_client(email, password):
    client = Garmin(email=email, password=password)
    client.login()
    logger.info("Garmin: авторизация успешна")
    return client


def fetch_day(client, d):
    date_str = d.isoformat()
    result   = {}
    for key, fn in [
        ("activities",   lambda: client.get_activities_by_date(date_str, date_str)),
        ("sleep",        lambda: client.get_sleep_data(date_str)),
        ("hrv",          lambda: client.get_hrv_data(date_str)),
        ("stats",        lambda: client.get_stats(date_str)),
        ("body_battery", lambda: client.get_body_battery(date_str, date_str)),
    ]:
        try:
            result[key] = fn()
        except Exception as e:
            logger.warning("Garmin " + key + " " + date_str + ": " + str(e))
            result[key] = None
    return result


def parse_sleep(raw, d):
    if not raw or not raw.get("dailySleepDTO"):
        return None
    dto = raw["dailySleepDTO"]
    def s(v): return int(v) if v else None
    return {
        "sleep_date":          d.isoformat(),
        "start_time":          dto.get("sleepStartTimestampLocal"),
        "end_time":            dto.get("sleepEndTimestampLocal"),
        "duration_seconds":    s(dto.get("sleepTimeSeconds")),
        "deep_sleep_seconds":  s(dto.get("deepSleepSeconds")),
        "light_sleep_seconds": s(dto.get("lightSleepSeconds")),
        "rem_sleep_seconds":   s(dto.get("remSleepSeconds")),
        "awake_seconds":       s(dto.get("awakeSleepSeconds")),
        "sleep_score":         dto.get("sleepScores", {}).get("overall", {}).get("value"),
        "avg_spo2":            raw.get("averageSpO2Value"),
        "avg_respiration":     raw.get("averageRespirationValue"),
        "raw_json":            json.dumps(raw, ensure_ascii=False),
    }


def parse_hrv(raw, d):
    if not raw or not raw.get("hrvSummary"):
        return None
    s = raw["hrvSummary"]
    return {
        "measurement_date":     d.isoformat(),
        "weekly_avg":           s.get("weeklyAvg"),
        "last_night_avg":       s.get("lastNight"),
        "last_night_5min_high": s.get("lastNight5MinHigh"),
        "status":               s.get("status"),
        "raw_json":             json.dumps(raw, ensure_ascii=False),
    }


def parse_daily_stats(raw, d):
    if not raw:
        return None
    return {
        "stat_date":             d.isoformat(),
        "resting_heart_rate":    raw.get("restingHeartRate"),
        "total_steps":           raw.get("totalSteps"),
        "total_distance_meters": raw.get("totalDistanceMeters"),
        "active_calories":       raw.get("activeKilocalories"),
        "bmr_calories":          raw.get("bmrKilocalories"),
        "stress_avg":            raw.get("averageStressLevel"),
        "body_battery_high":     raw.get("bodyBatteryHighestValue"),
        "body_battery_low":      raw.get("bodyBatteryLowestValue"),
        "raw_json":              json.dumps(raw, ensure_ascii=False),
    }

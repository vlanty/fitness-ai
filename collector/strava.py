import requests
import logging
import json
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"


def get_access_token(client_id, client_secret, refresh_token):
    resp = requests.post(STRAVA_TOKEN_URL, data={
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type":    "refresh_token",
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_activities(access_token, after, before):
    after_ts  = int(datetime(after.year,  after.month,  after.day,  tzinfo=timezone.utc).timestamp())
    before_ts = int(datetime(before.year, before.month, before.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    headers    = {"Authorization": "Bearer " + access_token}
    activities = []
    page       = 1
    while True:
        resp = requests.get(
            STRAVA_API_BASE + "/athlete/activities",
            headers=headers,
            params={"after": after_ts, "before": before_ts, "per_page": 100, "page": page},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        activities.extend(batch)
        page += 1
        logger.info("Strava: получено " + str(len(activities)) + " активностей")
    return activities


def _int(v):
    return int(round(v)) if v is not None else None


def parse_activity(raw):
    start_dt = datetime.fromisoformat(raw["start_date"].replace("Z", "+00:00"))
    dist     = raw.get("distance", 0) or 0
    duration = raw.get("moving_time", 0) or 0
    pace     = (duration / (dist / 1000)) if dist > 0 else None
    return {
        "activity_id":         str(raw["id"]),
        "source":              "strava",
        "activity_date":       start_dt.date().isoformat(),
        "start_time":          start_dt.isoformat(),
        "name":                raw.get("name"),
        "type":                raw.get("type"),
        "distance_meters":     float(dist),
        "duration_seconds":    _int(duration),
        "elevation_gain":      float(raw.get("total_elevation_gain") or 0),
        "avg_heart_rate":      _int(raw.get("average_heartrate")),
        "max_heart_rate":      _int(raw.get("max_heartrate")),
        "avg_pace_sec_per_km": pace,
        "calories":            _int(raw.get("calories")),
        "raw_json":            json.dumps(raw, ensure_ascii=False),
    }

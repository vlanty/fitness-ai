#!/bin/bash
set -e

mkdir -p collector

cat > collector/Dockerfile << 'EOF'
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
EOF

cat > collector/requirements.txt << 'EOF'
garminconnect==0.2.22
garth==0.4.45
requests==2.31.0
google-cloud-bigquery==3.17.2
google-cloud-secret-manager==2.20.0
EOF

cat > collector/strava.py << 'PYEOF'
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
        "distance_meters":     dist,
        "duration_seconds":    duration,
        "elevation_gain":      raw.get("total_elevation_gain"),
        "avg_heart_rate":      raw.get("average_heartrate"),
        "max_heart_rate":      raw.get("max_heartrate"),
        "avg_pace_sec_per_km": pace,
        "calories":            raw.get("calories"),
        "raw_json":            json.dumps(raw, ensure_ascii=False),
    }
PYEOF

cat > collector/garmin.py << 'PYEOF'
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
PYEOF

cat > collector/bq_writer.py << 'PYEOF'
import logging
from google.cloud import bigquery

logger = logging.getLogger(__name__)


class BigQueryWriter:
    def __init__(self, project_id, dataset_id):
        self.client     = bigquery.Client(project=project_id)
        self.project_id = project_id
        self.dataset_id = dataset_id

    def _table(self, name):
        return self.project_id + "." + self.dataset_id + "." + name

    def upsert_activities(self, rows):  self._merge(rows, "activities",  "activity_id")
    def upsert_sleep(self, rows):       self._merge(rows, "sleep",        "sleep_date")
    def upsert_hrv(self, rows):         self._merge(rows, "hrv",          "measurement_date")
    def upsert_daily_stats(self, rows): self._merge(rows, "daily_stats",  "stat_date")

    def _merge(self, rows, table_name, key_field):
        if not rows:
            return
        table_ref = self._table(table_name)
        stage_ref = table_ref + "_stage"
        job_cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE")
        self.client.load_table_from_json(rows, stage_ref, job_config=job_cfg).result()
        cols        = list(rows[0].keys())
        update_set  = ", ".join("T." + c + " = S." + c for c in cols if c != key_field)
        insert_cols = ", ".join(cols)
        insert_vals = ", ".join("S." + c for c in cols)
        self.client.query(
            "MERGE `" + table_ref + "` T USING `" + stage_ref + "` S "
            "ON T." + key_field + " = S." + key_field + " "
            "WHEN MATCHED THEN UPDATE SET " + update_set + " "
            "WHEN NOT MATCHED THEN INSERT (" + insert_cols + ") VALUES (" + insert_vals + ")"
        ).result()
        logger.info("BQ " + table_name + ": upsert " + str(len(rows)) + " строк")
PYEOF

cat > collector/main.py << 'PYEOF'
import os
import logging
from datetime import date, timedelta
from strava import get_access_token, get_activities, parse_activity
from garmin import get_client as garmin_login, fetch_day, parse_sleep, parse_hrv, parse_daily_stats
from bq_writer import BigQueryWriter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    project_id  = os.environ["GCP_PROJECT"]
    bq_dataset  = os.environ["BQ_DATASET"]
    full_sync   = os.environ.get("FULL_SYNC", "false").lower() == "true"

    strava_client_id     = os.environ["STRAVA_CLIENT_ID"]
    strava_client_secret = os.environ["STRAVA_CLIENT_SECRET"]
    strava_refresh_token = os.environ["STRAVA_REFRESH_TOKEN"]
    garmin_email         = os.environ["GARMIN_EMAIL"]
    garmin_password      = os.environ["GARMIN_PASSWORD"]

    bq = BigQueryWriter(project_id, bq_dataset)

    start_date = date(2020, 1, 1) if full_sync else date.today() - timedelta(days=1)
    end_date   = date.today() - timedelta(days=1)
    logger.info("Синхронизация " + str(start_date) + " -> " + str(end_date))

    try:
        token  = get_access_token(strava_client_id, strava_client_secret, strava_refresh_token)
        raw    = get_activities(token, start_date, end_date)
        parsed = [parse_activity(a) for a in raw]
        bq.upsert_activities(parsed)
        logger.info("Strava: сохранено " + str(len(parsed)) + " активностей")
    except Exception as e:
        logger.error("Strava сбой: " + str(e))

    try:
        garmin     = garmin_login(garmin_email, garmin_password)
        sleep_rows = []
        hrv_rows   = []
        stat_rows  = []
        current    = start_date
        while current <= end_date:
            day = fetch_day(garmin, current)
            if day.get("activities"):
                acts = []
                for a in day["activities"]:
                    acts.append({
                        "activity_id":         str(a.get("activityId", "")),
                        "source":              "garmin",
                        "activity_date":       current.isoformat(),
                        "start_time":          a.get("startTimeLocal"),
                        "name":                a.get("activityName"),
                        "type":                a.get("activityType", {}).get("typeKey"),
                        "distance_meters":     a.get("distance"),
                        "duration_seconds":    int(a.get("duration", 0)),
                        "elevation_gain":      a.get("elevationGain"),
                        "avg_heart_rate":      a.get("averageHR"),
                        "max_heart_rate":      a.get("maxHR"),
                        "avg_pace_sec_per_km": None,
                        "calories":            a.get("calories"),
                        "raw_json":            str(a),
                    })
                bq.upsert_activities(acts)
            if s := parse_sleep(day.get("sleep"), current): sleep_rows.append(s)
            if h := parse_hrv(day.get("hrv"), current):     hrv_rows.append(h)
            if st := parse_daily_stats(day.get("stats"), current): stat_rows.append(st)
            current += timedelta(days=1)

        bq.upsert_sleep(sleep_rows)
        bq.upsert_hrv(hrv_rows)
        bq.upsert_daily_stats(stat_rows)
        logger.info("Garmin: sleep=" + str(len(sleep_rows)) + " hrv=" + str(len(hrv_rows)) + " stats=" + str(len(stat_rows)))
    except Exception as e:
        logger.error("Garmin сбой: " + str(e), exc_info=True)

    logger.info("Синхронизация завершена")


if __name__ == "__main__":
    main()
PYEOF

echo "✅ collector/ создан:"
ls -1 collector/
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

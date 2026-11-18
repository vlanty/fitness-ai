"""Read-only queries for the bot. Only the unified views are used, so the bot
does not care which data sources are enabled."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from google.cloud import bigquery

from i18n import t

STALE_AFTER = timedelta(hours=26)
# Garmin keeps separate HR zones per sport; running ones go first.
RUN_FIRST = "IFNULL(type, '') LIKE '%running%'"


class BigQueryReader:
    def __init__(self, project_id: str, dataset_id: str, client: bigquery.Client | None = None) -> None:
        self.client = client or bigquery.Client(project=project_id)
        self.prefix = f"{project_id}.{dataset_id}"

    def _q(self, sql: str, **params) -> list[dict]:
        types = {date: "DATE", int: "INT64", str: "STRING"}
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter(k, types[type(v)], v) for k, v in params.items()])
        return [dict(row) for row in self.client.query(sql, job_config=cfg).result()]

    def get_last_activity(self) -> dict | None:
        rows = self._q(f"SELECT * FROM `{self.prefix}.activities_unified` "
                       "ORDER BY activity_date DESC, start_time DESC LIMIT 1")
        return rows[0] if rows else None

    def get_week_activities(self, weeks_back: int = 0) -> list[dict]:
        today = date.today()
        monday = today - timedelta(days=today.weekday() + weeks_back * 7)
        return self._q(f"SELECT * FROM `{self.prefix}.activities_unified` "
                       "WHERE activity_date BETWEEN @mon AND @sun ORDER BY activity_date, start_time",
                       mon=monday, sun=monday + timedelta(days=6))

    def get_health_last_days(self, days: int = 14) -> list[dict]:
        return self._q(f"SELECT * FROM `{self.prefix}.health_daily_unified` "
                       "WHERE date >= @since ORDER BY date",
                       since=date.today() - timedelta(days=days))

    def get_load_last_days(self, days: int = 1) -> list[dict]:
        return self._q(f"SELECT * FROM `{self.prefix}.training_load_daily` "
                       "WHERE date > @since ORDER BY date",
                       since=date.today() - timedelta(days=days))

    # ---------- analytics (stage 3) ----------

    RECORD_DISTANCES = (1000, 1609, 5000, 10000, 21098, 42195)

    def get_records(self, since: date) -> list[dict]:
        """Best split per distance, all-time and since `since`, from Garmin's fastestSplit_<m>.
        Splits faster than 2:30/km are treated as GPS glitches."""
        cases = " ".join(f"WHEN {d} THEN JSON_VALUE(raw_json, '$.fastestSplit_{d}')" for d in self.RECORD_DISTANCES)
        dists = ", ".join(map(str, self.RECORD_DISTANCES))
        return self._q(f"""
            WITH s AS (
              SELECT d, activity_date, name, SAFE_CAST(CASE d {cases} END AS FLOAT64) AS secs
              FROM `{self.prefix}.activities`, UNNEST([{dists}]) AS d
              WHERE source = 'garmin' AND IFNULL(type, '') LIKE '%running%'),
            ok AS (SELECT * FROM s WHERE secs > 0 AND secs / d * 1000 >= 150),
            best AS (
              SELECT d, 'all' AS span, activity_date, name, secs,
                     ROW_NUMBER() OVER (PARTITION BY d ORDER BY secs) AS rn FROM ok
              UNION ALL
              SELECT d, 'recent', activity_date, name, secs,
                     ROW_NUMBER() OVER (PARTITION BY d ORDER BY secs) FROM ok WHERE activity_date >= @since)
            SELECT d, span, activity_date, name, secs FROM best WHERE rn = 1 ORDER BY d, span""", since=since)

    def get_period(self, start: date, end: date) -> dict:
        """Totals for [start, end]: all activities, running, time in HR zones (Garmin)."""
        rows = self._q(f"""
            WITH a AS (
              SELECT *, IFNULL(type, '') LIKE '%running%' AS is_run
              FROM `{self.prefix}.activities_unified`
              WHERE activity_date BETWEEN @start AND @end),
            z AS (
              SELECT {", ".join(f"SUM(SAFE_CAST(JSON_VALUE(raw_json, '$.hrTimeInZone_{i}') AS FLOAT64)) AS z{i}" for i in range(1, 6))}
              FROM `{self.prefix}.activities`
              WHERE source = 'garmin' AND activity_date BETWEEN @start AND @end)
            SELECT
              COUNT(*) AS n, COUNTIF(is_run) AS runs,
              SUM(IFNULL(duration_seconds, 0)) AS secs,
              SUM(IFNULL(elevation_gain, 0)) AS elev,
              SUM(IFNULL(training_load, 0)) AS load,
              SUM(IF(is_run, distance_meters, 0)) / 1000 AS run_km,
              SUM(IF(is_run, duration_seconds, 0)) AS run_secs,
              SAFE_DIVIDE(SUM(IF(is_run AND avg_heart_rate IS NOT NULL, avg_heart_rate * duration_seconds, 0)),
                          SUM(IF(is_run AND avg_heart_rate IS NOT NULL, duration_seconds, 0))) AS run_hr,
              ARRAY_AGG(STRUCT(type, 1 AS c)) AS types,
              ANY_VALUE(z1) AS z1, ANY_VALUE(z2) AS z2, ANY_VALUE(z3) AS z3, ANY_VALUE(z4) AS z4, ANY_VALUE(z5) AS z5
            FROM a CROSS JOIN z""", start=start, end=end)
        r = rows[0] if rows else {}
        by_type: dict[str, int] = {}
        for item in r.pop("types", None) or []:
            by_type[item["type"] or "other"] = by_type.get(item["type"] or "other", 0) + 1
        r["by_type"] = by_type
        return r

    def get_weekly_run_km(self, weeks: int = 16) -> list[dict]:
        monday = date.today() - timedelta(days=date.today().weekday())
        return self._q(f"""
            SELECT w AS week, IFNULL(SUM(distance_meters) / 1000, 0) AS km
            FROM UNNEST(GENERATE_DATE_ARRAY(DATE_SUB(@monday, INTERVAL {weeks - 1} WEEK), @monday, INTERVAL 1 WEEK)) AS w
            LEFT JOIN `{self.prefix}.activities_unified` a
              ON DATE_TRUNC(a.activity_date, WEEK(MONDAY)) = w AND IFNULL(a.type, '') LIKE '%running%'
            GROUP BY w ORDER BY w""", monday=monday)

    def get_load_series(self, days: int = 120) -> list[dict]:
        return self._q(f"SELECT date, acute_load, chronic_load FROM `{self.prefix}.training_load_daily` "
                       "WHERE date > @since ORDER BY date", since=date.today() - timedelta(days=days))

    def get_health_series(self, days: int = 90) -> list[dict]:
        return self._q(f"SELECT date, hrv_last_night, hrv_baseline_low, hrv_baseline_high, resting_hr "
                       f"FROM `{self.prefix}.health_daily_unified` WHERE date > @since ORDER BY date",
                       since=date.today() - timedelta(days=days))

    def get_run_efficiency(self, days: int = 180) -> list[dict]:
        """Per run: metres per heartbeat (speed / HR). Higher = more economical at the same effort."""
        return self._q(f"""
            SELECT activity_date AS date,
                   (distance_meters / duration_seconds * 60) / avg_heart_rate AS m_per_beat
            FROM `{self.prefix}.activities_unified`
            WHERE IFNULL(type, '') LIKE '%running%' AND activity_date > @since
              AND distance_meters >= 3000 AND duration_seconds > 0 AND avg_heart_rate >= 90
            ORDER BY activity_date""", since=date.today() - timedelta(days=days))

    def get_athlete_inputs(self) -> dict:
        """Watch HR zones from the latest .FIT, the threshold ~90 days earlier, and Garmin's
        7-day resting HR. Missing parts are None (e.g. Strava-only installs have no zones).
        Garmin keeps zones per sport: running zones are preferred (the coach is a running coach),
        any other activity's zones are the fallback."""
        rows = self._q(f"""
            WITH z AS (
              SELECT activity_date AS zones_date, type AS zones_type, hr_zone_calc, hr_zone_bounds, lthr
              FROM `{self.prefix}.activities`
              WHERE hr_zone_bounds IS NOT NULL
              ORDER BY {RUN_FIRST} DESC, start_time DESC LIMIT 1),
            old AS (
              SELECT lthr AS lthr_then, activity_date AS lthr_then_date
              FROM `{self.prefix}.activities`
              WHERE lthr IS NOT NULL AND activity_date <= DATE_SUB(@today, INTERVAL 30 DAY)
              ORDER BY {RUN_FIRST} DESC, ABS(DATE_DIFF(activity_date, DATE_SUB(@today, INTERVAL 90 DAY), DAY)) LIMIT 1),
            r AS (
              SELECT CAST(ROUND(AVG(resting_hr)) AS INT64) AS resting_hr_7d
              FROM `{self.prefix}.health_daily_unified`
              WHERE date > DATE_SUB(@today, INTERVAL 7 DAY) AND resting_hr IS NOT NULL)
            SELECT * FROM r LEFT JOIN z ON TRUE LEFT JOIN old ON TRUE""", today=date.today())
        return rows[0] if rows else {}

    def get_source_status(self) -> list[dict]:
        return self._q(f"SELECT source, cursor_date, last_success_at, last_status, last_error "
                       f"FROM `{self.prefix}.ingest_state` ORDER BY source")


def stale_warnings(status_rows: list[dict], enabled: list[str], lang: str = "en",
                   now: datetime | None = None) -> list[str]:
    """One line per enabled source that has not synced successfully for > STALE_AFTER."""
    now = now or datetime.now(timezone.utc)
    by_source = {r["source"]: r for r in status_rows}
    out = []
    for name in enabled:
        r = by_source.get(name)
        last = r.get("last_success_at") if r else None
        if last is None:
            out.append(t(lang, "stale.never", source=name))
        elif now - last > STALE_AFTER:
            status = f" ({r['last_status']})" if r.get("last_status") not in (None, "ok") else ""
            out.append(t(lang, "stale.old", source=name, when=f"{last:%d.%m %H:%M}", status=status))
    return out

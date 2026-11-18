"""Read-only queries for the bot. Only the unified views are used, so the bot
does not care which data sources are enabled."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from google.cloud import bigquery

from i18n import t

STALE_AFTER = timedelta(hours=26)


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

    def get_athlete_inputs(self) -> dict:
        """Watch HR zones from the latest .FIT, the threshold ~90 days earlier, and Garmin's
        7-day resting HR. Missing parts are None (e.g. Strava-only installs have no zones)."""
        rows = self._q(f"""
            WITH z AS (
              SELECT activity_date AS zones_date, hr_zone_calc, hr_zone_bounds, lthr
              FROM `{self.prefix}.activities`
              WHERE hr_zone_bounds IS NOT NULL
              ORDER BY start_time DESC LIMIT 1),
            old AS (
              SELECT lthr AS lthr_then, activity_date AS lthr_then_date
              FROM `{self.prefix}.activities`
              WHERE lthr IS NOT NULL AND activity_date <= DATE_SUB(@today, INTERVAL 30 DAY)
              ORDER BY ABS(DATE_DIFF(activity_date, DATE_SUB(@today, INTERVAL 90 DAY), DAY)) LIMIT 1),
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

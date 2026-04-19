from google.cloud import bigquery
from datetime import date, timedelta


class BigQueryReader:
    def __init__(self, project_id, dataset_id):
        self.client  = bigquery.Client(project=project_id)
        self.project = project_id
        self.dataset = dataset_id

    def _q(self, sql):
        return [dict(row) for row in self.client.query(sql).result()]

    def get_last_activity(self):
        rows = self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".activities` WHERE source = 'strava' ORDER BY activity_date DESC, start_time DESC LIMIT 1")
        return rows[0] if rows else None

    def get_week_activities(self, weeks_back=0):
        today  = date.today()
        monday = today - timedelta(days=today.weekday() + weeks_back * 7)
        sunday = monday + timedelta(days=6)
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".activities` WHERE activity_date BETWEEN '" + str(monday) + "' AND '" + str(sunday) + "' ORDER BY activity_date")

    def get_hrv_last_days(self, days=14):
        since = str(date.today() - timedelta(days=days))
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".hrv` WHERE measurement_date >= '" + since + "' ORDER BY measurement_date")

    def get_sleep_last_days(self, days=7):
        since = str(date.today() - timedelta(days=days))
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".sleep` WHERE sleep_date >= '" + since + "' ORDER BY sleep_date")

    def get_daily_stats_last_days(self, days=14):
        since = str(date.today() - timedelta(days=days))
        return self._q("SELECT * FROM `" + self.project + "." + self.dataset + ".daily_stats` WHERE stat_date >= '" + since + "' ORDER BY stat_date")

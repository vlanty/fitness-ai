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

        # Берём схему из основной таблицы чтобы staging совпадал по типам
        main_table = self.client.get_table(table_ref)
        job_cfg = bigquery.LoadJobConfig(
            write_disposition="WRITE_TRUNCATE",
            schema=main_table.schema,
        )
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

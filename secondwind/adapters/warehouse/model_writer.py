"""Writes the universal data model (dataset `secondwind`) to BigQuery.

A writer belongs to one user: every row is stamped with its user_id and rows of another user
are refused. Keyed tables are loaded to a staging table and MERGEd (idempotent); streams are
replaced per session.
"""

from __future__ import annotations

import logging
import uuid

from google.cloud import bigquery

from secondwind.adapters.warehouse.bigquery_writer import dedupe, merge_sql
from secondwind.core.observations import ModelBatch

logger = logging.getLogger(__name__)

KEYS = {
    "observations": ("observation_id",),
    "sessions": ("session_id",),
    "hr_zones": ("user_id", "session_id", "source", "method"),
    "raw_payloads": ("object_path",),
}


class BigQueryModelWriter:
    def __init__(self, project_id: str, dataset_id: str, user_id: str,
                 client: bigquery.Client | None = None) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.client = client or bigquery.Client(project=project_id)
        self.prefix = f"{project_id}.{dataset_id}"
        self.user_id = user_id

    def table(self, name: str) -> str:
        return f"{self.prefix}.{name}"

    def _own(self, rows: list[dict]) -> list[dict]:
        if any(r.get("user_id") not in (None, self.user_id) for r in rows):
            raise ValueError(f"rows for another user passed to the writer of {self.user_id}")
        return [{**r, "user_id": self.user_id} for r in rows]

    def _load(self, table_name: str, rows: list[dict], disposition: str) -> tuple[str, list[str]]:
        target = self.client.get_table(self.table(table_name))
        cols = [f.name for f in target.schema]
        rows = [{c: r.get(c) for c in cols} for r in rows]
        stage = self.table(f"_stage_{table_name}_{uuid.uuid4().hex[:8]}")
        cfg = bigquery.LoadJobConfig(write_disposition=disposition, schema=target.schema)
        self.client.load_table_from_json(rows, stage, job_config=cfg).result()
        return stage, cols

    def upsert(self, table_name: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        key = KEYS[table_name]
        rows = dedupe(self._own(rows), key)
        stage, cols = self._load(table_name, rows, "WRITE_TRUNCATE")
        try:
            self.client.query(merge_sql(self.table(table_name), stage, cols, key)).result()
        finally:
            self.client.delete_table(stage, not_found_ok=True)
        logger.info("BQ %s: upserted %d rows", table_name, len(rows))
        return len(rows)

    def replace_streams(self, rows: list[dict]) -> int:
        """Streams are large and re-derived from the file: replace them per session."""
        if not rows:
            return 0
        rows = self._own(rows)
        sessions = sorted({r["session_id"] for r in rows if r.get("session_id")})
        stage, cols = self._load("streams", rows, "WRITE_TRUNCATE")
        try:
            self.client.query(
                f"DELETE FROM `{self.table('streams')}` WHERE user_id = @uid AND session_id IN UNNEST(@s)",
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("uid", "STRING", self.user_id),
                    bigquery.ArrayQueryParameter("s", "STRING", sessions)])).result()
            self.client.query(f"INSERT INTO `{self.table('streams')}` ({', '.join(cols)}) "
                              f"SELECT {', '.join(cols)} FROM `{stage}`").result()
        finally:
            self.client.delete_table(stage, not_found_ok=True)
        logger.info("BQ streams: %d samples for %d sessions", len(rows), len(sessions))
        return len(rows)

    def write(self, batch: ModelBatch) -> dict[str, int]:
        return {
            "sessions": self.upsert("sessions", batch.sessions),
            "observations": self.upsert("observations", batch.observations),
            "hr_zones": self.upsert("hr_zones", batch.hr_zones),
            "streams": self.replace_streams(batch.streams),
            "raw_payloads": self.upsert("raw_payloads", batch.raw_payloads),
        }

    def stored_file_ids(self, source: str, start: str) -> set[str]:
        """Vendor ids of workouts whose file is already stored (skip re-downloading)."""
        job = self.client.query(
            f"SELECT vendor_id FROM `{self.table('sessions')}` "
            "WHERE user_id = @uid AND source = @s AND kind = 'workout' AND local_date >= @d "
            "AND JSON_VALUE(attrs, '$.fit_ref') IS NOT NULL",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("uid", "STRING", self.user_id),
                bigquery.ScalarQueryParameter("s", "STRING", source),
                bigquery.ScalarQueryParameter("d", "DATE", start),
            ]))
        return {r.vendor_id for r in job.result()}

"""Idempotent writes to BigQuery: load to a staging table, then MERGE on a composite key.

A writer belongs to one user: it stamps `user_id` on every row and the MERGE key starts with it."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from google.cloud import bigquery

logger = logging.getLogger(__name__)

KEYS = {
    "activities": ("user_id", "source", "activity_id"),
    "health_daily": ("user_id", "source", "date"),
}


def dedupe(rows: list[dict], key: tuple[str, ...]) -> list[dict]:
    """Last row wins per key: MERGE fails if two source rows hit one target row."""
    out: dict[tuple, dict] = {}
    for r in rows:
        out[tuple(r.get(k) for k in key)] = r
    return list(out.values())


def merge_sql(table: str, stage: str, cols: list[str], key: tuple[str, ...]) -> str:
    on = " AND ".join(f"T.{k} = S.{k}" for k in key)
    # COALESCE: a re-read without a value (e.g. no new .FIT downloaded) never erases a stored one.
    update = ", ".join(f"T.{c} = COALESCE(S.{c}, T.{c})" for c in cols if c not in key)
    ins_cols = ", ".join(cols)
    ins_vals = ", ".join(f"S.{c}" for c in cols)
    return (f"MERGE `{table}` T USING `{stage}` S ON {on} "
            f"WHEN MATCHED THEN UPDATE SET {update} "
            f"WHEN NOT MATCHED THEN INSERT ({ins_cols}) VALUES ({ins_vals})")


class BigQueryWriter:
    def __init__(self, project_id: str, dataset_id: str, user_id: str,
                 client: bigquery.Client | None = None) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.client = client or bigquery.Client(project=project_id)
        self.prefix = f"{project_id}.{dataset_id}"
        self.user_id = user_id

    def table(self, name: str) -> str:
        return f"{self.prefix}.{name}"

    def upsert(self, table_name: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        key = KEYS[table_name]
        if any(r.get("user_id") not in (None, self.user_id) for r in rows):
            raise ValueError(f"rows for another user passed to the writer of {self.user_id}")
        rows = [{**r, "user_id": self.user_id} for r in rows]
        target = self.client.get_table(self.table(table_name))
        cols = [f.name for f in target.schema]
        now = datetime.now(timezone.utc).isoformat()
        rows = [{c: ({**r, "ingested_at": r.get("ingested_at", now)}).get(c) for c in cols}
                for r in dedupe(rows, key)]

        stage = self.table(f"_stage_{table_name}_{uuid.uuid4().hex[:8]}")
        job_cfg = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", schema=target.schema)
        self.client.load_table_from_json(rows, stage, job_config=job_cfg).result()
        try:
            self.client.query(merge_sql(self.table(table_name), stage, cols, key)).result()
        finally:
            self.client.delete_table(stage, not_found_ok=True)
        logger.info("BQ %s: upserted %d rows", table_name, len(rows))
        return len(rows)

    def stored_file_ids(self, source: str, start: str) -> set[str]:
        """Activity ids whose raw file is already in GCS (skip re-downloading)."""
        job = self.client.query(
            f"SELECT activity_id FROM `{self.table('activities')}` "
            "WHERE user_id = @uid AND source = @s AND raw_file IS NOT NULL AND activity_date >= @d",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("uid", "STRING", self.user_id),
                bigquery.ScalarQueryParameter("s", "STRING", source),
                bigquery.ScalarQueryParameter("d", "DATE", start),
            ]))
        return {r.activity_id for r in job.result()}

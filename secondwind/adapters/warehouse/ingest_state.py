"""Per-source sync cursor in BigQuery table `ingest_state` (one row per source)."""

from __future__ import annotations

from datetime import date

from google.cloud import bigquery


class IngestState:
    def __init__(self, client: bigquery.Client, table: str) -> None:
        self.client, self.table = client, table

    def _params(self, **kw):
        types = {"source": "STRING", "cursor": "DATE", "status": "STRING", "error": "STRING"}
        return bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter(k, types[k], v) for k, v in kw.items()])

    def cursor(self, source: str) -> date | None:
        rows = list(self.client.query(
            f"SELECT cursor_date FROM `{self.table}` WHERE source = @source",
            job_config=self._params(source=source)).result())
        return rows[0].cursor_date if rows else None

    def record(self, source: str, cursor: date | None, status: str, error: str | None = None) -> None:
        """status: ok | partial | failed. Cursor only moves on success; last_success_at drives bot warnings."""
        self.client.query(
            f"""MERGE `{self.table}` T
                USING (SELECT @source AS source) S ON T.source = S.source
                WHEN MATCHED THEN UPDATE SET
                  cursor_date = IF(@status = 'ok', @cursor, T.cursor_date),
                  last_run_at = CURRENT_TIMESTAMP(),
                  last_success_at = IF(@status = 'ok', CURRENT_TIMESTAMP(), T.last_success_at),
                  last_status = @status, last_error = @error
                WHEN NOT MATCHED THEN INSERT
                  (source, cursor_date, last_run_at, last_success_at, last_status, last_error)
                  VALUES (@source, IF(@status = 'ok', @cursor, NULL), CURRENT_TIMESTAMP(),
                          IF(@status = 'ok', CURRENT_TIMESTAMP(), NULL), @status, @error)""",
            job_config=self._params(source=source, cursor=cursor, status=status, error=error)).result()

"""Collector job: pull every enabled source into BigQuery.

Env:
  GCP_PROJECT, BQ_DATASET      target dataset
  DATA_SOURCES                 comma list, e.g. "garmin,strava" (order = priority, used by views)
  RAW_BUCKET                   GCS bucket for raw files (.FIT); optional
  BACKFILL_FROM=YYYY-MM-DD     one-off history load from this date (ignores cursor)
  FIRST_SYNC_DAYS              days to load when a source has no cursor yet (default 30)
  <source secrets>             see each source's required_env

Exit code is non-zero if any source failed, so failures are visible in Cloud Run.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta

from secondwind.adapters.warehouse.bigquery_writer import BigQueryWriter
from secondwind.apps.collector.sources import REGISTRY, Batch, PartialFetch
from secondwind.adapters.warehouse.ingest_state import IngestState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("collector")


def windows(start: date, end: date, size: int):
    while start <= end:
        stop = min(end, start + timedelta(days=size - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def save(bq: BigQueryWriter, bucket, batch: Batch) -> None:
    # Files first: an activity row only points at raw_file once the object exists.
    stored = set()
    for f in batch.files:
        if bucket is None:
            break
        bucket.blob(f.path).upload_from_string(f.content, content_type=f.content_type)
        stored.add(f.path)
    for a in batch.activities:
        if a.get("raw_file") and a["raw_file"] not in stored:
            a["raw_file"] = None
    bq.upsert("activities", batch.activities)
    bq.upsert("health_daily", batch.health_daily)


def run_source(name: str, env: dict, bq: BigQueryWriter, state: IngestState, bucket) -> bool:
    try:
        src = REGISTRY[name](env)
    except KeyError:
        logger.error("неизвестный источник %r (доступны: %s)", name, ", ".join(REGISTRY))
        return False
    except RuntimeError as e:
        logger.error("%s", e)
        state.record(name, None, "failed", str(e))
        return False

    today = date.today()
    backfill = env.get("BACKFILL_FROM")
    cursor = state.cursor(name)
    if backfill:
        start = date.fromisoformat(backfill)
    elif cursor:
        start = cursor - timedelta(days=src.lookback_days)
    else:
        start = today - timedelta(days=int(env.get("FIRST_SYNC_DAYS", "30")))
    logger.info("%s: %s … %s%s", name, start, today, " (backfill)" if backfill else "")

    ok = True
    for w_start, w_end in windows(start, today, src.max_window_days):
        known = bq.stored_file_ids(name, w_start.isoformat())
        try:
            batch = src.fetch(w_start, w_end, known)
        except PartialFetch as e:
            save(bq, bucket, e.batch)
            logger.error("%s %s…%s: частично: %s", name, w_start, w_end, e)
            state.record(name, None, "partial", str(e)[:1000])
            ok = False
            break
        except Exception as e:  # noqa: BLE001 - report any provider failure and keep other sources running
            logger.error("%s %s…%s: сбой: %s", name, w_start, w_end, e, exc_info=True)
            state.record(name, None, "failed", str(e)[:1000])
            ok = False
            break
        save(bq, bucket, batch)
        if not backfill:
            # Cursor = last fully synced day; next run re-reads lookback_days before it.
            state.record(name, w_end, "ok")
    if ok and backfill:
        state.record(name, max(today, cursor or today), "ok")
    return ok


def main() -> int:
    env = dict(os.environ)
    names = [s.strip() for s in env.get("DATA_SOURCES", "").split(",") if s.strip()]
    if not names:
        logger.error("DATA_SOURCES пуст — нечего собирать")
        return 2

    bq = BigQueryWriter(env["GCP_PROJECT"], env["BQ_DATASET"])
    state = IngestState(bq.client, bq.table("ingest_state"))
    bucket = None
    if env.get("RAW_BUCKET"):
        from google.cloud import storage
        bucket = storage.Client(project=env["GCP_PROJECT"]).bucket(env["RAW_BUCKET"])

    results = {n: run_source(n, env, bq, state, bucket) for n in names}
    logger.info("итог: %s", ", ".join(f"{n}={'ok' if r else 'СБОЙ'}" for n, r in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

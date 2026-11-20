"""Collector job: pull every enabled source into BigQuery.

Env:
  GCP_PROJECT, BQ_DATASET      target dataset (the universal model, `secondwind`)
  OWNER_USER_ID                whose data this is (solo mode: the one owner)
  DATA_SOURCES                 comma list, e.g. "garmin" (order = priority, used by views)
  RAW_BUCKET                   GCS bucket for raw payloads and files (.FIT); optional
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

import uuid

from secondwind.adapters.blob.raw_store import RawStore
from secondwind.adapters.warehouse.ingest_state import IngestState
from secondwind.adapters.warehouse.model_writer import BigQueryModelWriter
from secondwind.apps.collector.sources import REGISTRY, Batch, PartialFetch
from secondwind.core.ports import IngestStore, ModelWriter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("collector")


def windows(start: date, end: date, size: int):
    while start <= end:
        stop = min(end, start + timedelta(days=size - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def save(writer: ModelWriter, store: RawStore, src, batch: Batch, user_id: str) -> dict[str, int]:
    """Raw payloads to the bucket, the batch through the source's model, rows to the warehouse."""
    model = src.to_model(user_id, batch, store)
    return writer.write(model)


def run_source(name: str, env: dict, bq: ModelWriter, state: IngestStore, bucket, user_id: str,
               run_id: str | None = None) -> bool:
    try:
        src = REGISTRY[name](env)
    except KeyError:
        logger.error("unknown source %r (available: %s)", name, ", ".join(REGISTRY))
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
            save(bq, RawStore(bucket, user_id, name, run_id), src, e.batch, user_id)
            logger.error("%s %s…%s: partial: %s", name, w_start, w_end, e)
            state.record(name, None, "partial", str(e)[:1000])
            ok = False
            break
        except Exception as e:  # noqa: BLE001 - report any provider failure and keep other sources running
            logger.error("%s %s…%s: failed: %s", name, w_start, w_end, e, exc_info=True)
            state.record(name, None, "failed", str(e)[:1000])
            ok = False
            break
        save(bq, RawStore(bucket, user_id, name, run_id), src, batch, user_id)
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
        logger.error("DATA_SOURCES is empty: nothing to collect")
        return 2

    user_id = env.get("OWNER_USER_ID", "").strip()
    if not user_id:
        logger.error("OWNER_USER_ID is empty: unknown whose data this is")
        return 2

    bq = BigQueryModelWriter(env["GCP_PROJECT"], env["BQ_DATASET"], user_id)
    state = IngestState(bq.client, bq.table("ingest_state"), user_id)
    run_id = uuid.uuid4().hex[:12]
    bucket = None
    if env.get("RAW_BUCKET"):
        from google.cloud import storage
        bucket = storage.Client(project=env["GCP_PROJECT"]).bucket(env["RAW_BUCKET"])

    results = {n: run_source(n, env, bq, state, bucket, user_id, run_id) for n in names}
    logger.info("result: %s", ", ".join(f"{n}={'ok' if r else 'FAILED'}" for n, r in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

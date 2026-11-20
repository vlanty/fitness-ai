#!/usr/bin/env python3
"""One-off: fill the watch HR-zone columns for activities whose .FIT is already stored.

New activities get these columns from the collector; this covers the history.
Reads .FIT archives from the raw bucket and MERGEs only the zone columns
(other columns are kept by the MERGE's COALESCE). Safe to re-run: activities
that already have zones are skipped.

  pip install -r requirements/collector.txt
  python3 scripts/backfill_fit_zones.py --project YOUR_PROJECT [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google.cloud import bigquery, storage  # noqa: E402

from secondwind.adapters.warehouse.bigquery_writer import BigQueryWriter  # noqa: E402
from secondwind.apps.collector.fit_zones import extract_zones  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--dataset", default="fitness")
    ap.add_argument("--bucket", help="raw bucket (default <project>-fitness-raw)")
    ap.add_argument("--dry-run", action="store_true", help="read and parse, don't write")
    args = ap.parse_args()

    bq = bigquery.Client(project=args.project)
    bucket = storage.Client(project=args.project).bucket(args.bucket or f"{args.project}-fitness-raw")
    todo = list(bq.query(
        f"SELECT user_id, source, activity_id, activity_date, raw_file FROM `{args.project}.{args.dataset}.activities` "
        "WHERE raw_file IS NOT NULL AND hr_zone_bounds IS NULL AND user_id IS NOT NULL ORDER BY activity_date").result())
    print(f"activities to scan: {len(todo)}")

    rows, without = [], 0
    for i, r in enumerate(todo, 1):
        zones = extract_zones(bucket.blob(r.raw_file).download_as_bytes())
        if zones:
            # ingested_at None: the MERGE keeps the original load time.
            rows.append({"user_id": r.user_id, "source": r.source, "activity_id": r.activity_id,
                         "activity_date": r.activity_date.isoformat(), "ingested_at": None, **zones})
        else:
            without += 1
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}")
    print(f"with zones: {len(rows)}, without: {without}")
    if rows:
        lthr = [(r["activity_date"], r["lthr"]) for r in rows if r.get("lthr")]
        if lthr:
            print(f"LTHR first {lthr[0]}, last {lthr[-1]}, distinct values: {sorted({v for _, v in lthr})}")
    if args.dry_run or not rows:
        return
    written = 0
    for uid in sorted({r["user_id"] for r in rows}):
        mine = [r for r in rows if r["user_id"] == uid]
        written += BigQueryWriter(args.project, args.dataset, uid, client=bq).upsert("activities", mine)
    print(f"written: {written}")


if __name__ == "__main__":
    main()

"""Common interface every data source implements.

A source turns a date range into normalized rows:
  * activities   -> table `activities`   (key: source + activity_id)
  * health_daily -> table `health_daily` (key: source + date)
  * files        -> raw files (e.g. .FIT) stored in GCS, optional

To add a source: create secondwind/apps/collector/sources/<name>.py with a class that
subclasses Source, register it in sources/__init__.py, list its secrets in
terraform (local.source_secrets) and document it in docs/setup.md (see docs/how-it-works.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawFile:
    path: str           # object path inside the raw bucket, e.g. garmin/activities/123.zip
    content: bytes
    content_type: str = "application/octet-stream"


@dataclass
class Batch:
    activities: list[dict] = field(default_factory=list)
    health_daily: list[dict] = field(default_factory=list)
    files: list[RawFile] = field(default_factory=list)


class PartialFetch(Exception):
    """Some endpoints failed; carries what did succeed so it can still be saved."""

    def __init__(self, batch: Batch, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:5]))
        self.batch = batch


class Source:
    name: str = ""
    # Environment variables (filled from Secret Manager) the source needs.
    required_env: tuple[str, ...] = ()
    # How many days before the cursor to re-read: providers revise recent days
    # (sleep and HRV are often finalized later). MERGE keeps this idempotent.
    lookback_days: int = 3
    # Largest date window requested at once (backfill is split into chunks).
    max_window_days: int = 28

    def __init__(self, env: dict[str, str]) -> None:
        missing = [k for k in self.required_env if not env.get(k)]
        if missing:
            raise RuntimeError(f"{self.name}: не заданы переменные {', '.join(missing)}")
        self.env = env

    def fetch(self, start: date, end: date, known_activity_ids: set[str]) -> Batch:
        """Return everything for the inclusive range [start, end].

        known_activity_ids: ids already stored, so sources can skip expensive
        per-activity downloads (e.g. .FIT files) for them.
        """
        raise NotImplementedError


def to_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)

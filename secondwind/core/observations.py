"""Rows of the universal data model: observations and entities.

`observation()` is the only way plugins and modules create an observation row: it checks the
metric/method/unit against the registries, converts the value to the canonical unit, types it
(value_num / value_text / value_bool / value_json) and derives a deterministic id, so loading
the same data twice updates rows instead of duplicating them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from secondwind.core.registry import Registry, registry

# Fields that identify an observation (docs: architecture 13.10). as_of_ts joins them only for
# metrics whose registry revision policy keeps every revision.
KEY_FIELDS = ("user_id", "source", "origin_app", "metric", "method", "method_version",
              "vendor_field", "start_ts", "end_ts", "session_id", "local_date")


def _iso(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def stable_id(*parts) -> str:
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()[:32]


def observation(*, user_id: str, source: str, metric: str, method: str, value, unit: str | None,
                local_date, computed_by: str = "vendor", reg: Registry | None = None,
                **extra) -> dict:
    """One observation row; raises registry.UnknownCode if the registries do not describe it."""
    reg = reg or registry()
    meta = reg.metric(metric)
    row = {"user_id": user_id, "source": source, "metric": metric, "method": method,
           "local_date": _iso(local_date), "computed_by": computed_by,
           "ingested_at": datetime.now(timezone.utc).isoformat(),
           "value_num": None, "value_text": None, "value_bool": None, "value_json": None}
    for k in ("start_ts", "end_ts", "as_of_ts"):
        if k in extra:
            extra[k] = _iso(extra[k])
    row.update(extra)

    vt = meta["value_type"]
    if vt == "num":
        canonical = meta["unit"]
        num = float(value)
        if unit != canonical:
            row["value_orig"], row["unit_orig"] = str(value), unit
            num, unit = reg.to_canonical(num, unit, metric)
        row["value_num"] = num
    elif vt == "text":
        row["value_text"] = str(value)
    elif vt == "bool":
        row["value_bool"] = bool(value)
    else:
        row["value_json"] = value
    row["unit"] = unit
    reg.check_observation(metric, method, unit if meta["unit"] is not None else None)

    key = [row.get(k) for k in KEY_FIELDS]
    if meta.get("revision") == "keep_revisions":
        key.append(row.get("as_of_ts"))
    row["observation_id"] = stable_id(*key)
    return row


@dataclass
class ModelBatch:
    """Everything one fetch window produced, in the universal model."""
    observations: list[dict] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)
    hr_zones: list[dict] = field(default_factory=list)
    streams: list[dict] = field(default_factory=list)
    raw_payloads: list[dict] = field(default_factory=list)

    def extend(self, other: "ModelBatch") -> None:
        for name in ("observations", "sessions", "hr_zones", "streams", "raw_payloads"):
            getattr(self, name).extend(getattr(other, name))


def to_json_value(v):
    """JSON column payloads are written as JSON text."""
    return v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False, sort_keys=True)

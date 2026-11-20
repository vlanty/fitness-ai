"""Registries: what metric, method, unit, sport and source codes mean.

The files live in secondwind/contracts/registry/*.yaml. Data rows carry only codes; everything
about their meaning is looked up here. `validate()` is run by the tests, so a code that is used
but not defined, or a reference that does not resolve, fails CI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path

import yaml

REGISTRY_DIR = Path(__file__).resolve().parent.parent / "contracts" / "registry"

VALUE_TYPES = {"num", "text", "bool", "json"}
VALUE_KINDS = {"absolute", "delta", "score", "count", "category"}
COMPARABLE = {"none", "within_method", "within_family"}
REVISION = {"immutable", "latest_wins", "keep_revisions"}
EVIDENCE = {"documented", "inferred", "unknown"}
STATUS = {"active", "deprecated"}
SOURCE_STATUS = {"supported", "planned", "unsupported"}
COLUMN_TYPES = {"INT64", "FLOAT64", "STRING"}
UNMAPPED_PREFIX = "x."          # x.<source>.<path>: a vendor field no plugin maps yet


class UnknownCode(KeyError):
    pass


@dataclass
class Registry:
    metrics: dict[str, dict]
    methods: dict[str, dict]
    units: dict[str, dict]
    sports: dict[str, dict]
    sources: dict[str, dict]
    origins: dict[str, dict]
    modules: dict[str, dict]
    errors: list[str] = field(default_factory=list)

    # ── lookups ───────────────────────────────────────────────────────────────
    def metric(self, code: str) -> dict:
        if code.startswith(UNMAPPED_PREFIX):
            return {"code": code, "value_type": "json", "unit": None, "value_kind": "category",
                    "group": "unmapped", "comparable": "none", "proprietary": True, "revision": "latest_wins"}
        try:
            return self.metrics[code]
        except KeyError:
            raise UnknownCode(f"unknown metric {code!r}") from None

    def method_allows(self, method: str, metric: str) -> bool:
        m = self.methods.get(method)
        return bool(m) and any(fnmatchcase(metric, p) for p in m["metrics"])

    def check_observation(self, metric: str, method: str, unit: str | None) -> None:
        """Raise if a plugin is about to write a row the registries do not describe."""
        meta = self.metric(metric)
        if not self.method_allows(method, metric):
            raise UnknownCode(f"method {method!r} is not defined for metric {metric!r}")
        if meta["unit"] is not None and unit != meta["unit"]:
            raise UnknownCode(f"{metric!r} must be stored in {meta['unit']!r}, got {unit!r}")

    def to_canonical(self, value: float, unit: str, metric: str) -> tuple[float, str]:
        """Convert a value to the metric's canonical unit (deltas use only the factor)."""
        target = self.metric(metric)["unit"]
        if unit == target:
            return value, unit
        u, t = self.units.get(unit), self.units.get(target)
        if not u or not t or u["dimension"] != t["dimension"]:
            raise UnknownCode(f"cannot convert {unit!r} to {target!r} for {metric!r}")
        scope = u.get("scope_metric")
        if scope and not fnmatchcase(metric, scope):
            raise UnknownCode(f"unit {unit!r} only applies to {scope!r}")
        conv = u.get("to_canonical") or {}
        if self.metric(metric)["value_kind"] == "delta":
            return value * u.get("delta_factor", conv.get("factor", 1)), target
        return value * conv.get("factor", 1) + conv.get("offset", 0), target

    def sport_for(self, source: str, vendor_type: str | None) -> str:
        for code, s in self.sports.items():
            if vendor_type in (s.get("mappings") or {}).get(source, []):
                return code
        return "other"

    # ── validation ────────────────────────────────────────────────────────────
    def validate(self) -> list[str]:
        err = list(self.errors)
        for code, m in self.metrics.items():
            if code.startswith(UNMAPPED_PREFIX):
                err.append(f"metric {code}: the x. prefix is reserved for unmapped vendor fields")
            if m.get("value_type") not in VALUE_TYPES:
                err.append(f"metric {code}: value_type {m.get('value_type')!r}")
            if m.get("unit") not in self.units:
                err.append(f"metric {code}: unknown unit {m.get('unit')!r}")
            elif not self.units[m["unit"]].get("canonical"):
                err.append(f"metric {code}: unit {m['unit']!r} is not canonical")
            if m.get("value_kind") not in VALUE_KINDS:
                err.append(f"metric {code}: value_kind {m.get('value_kind')!r}")
            if m.get("comparable") not in COMPARABLE:
                err.append(f"metric {code}: comparable {m.get('comparable')!r}")
            if m.get("revision") not in REVISION:
                err.append(f"metric {code}: revision {m.get('revision')!r}")
            if not m.get("group"):
                err.append(f"metric {code}: no group")
            if code.startswith("vendor_score.") and not m.get("proprietary"):
                err.append(f"metric {code}: vendor scores must be proprietary")
        for view in ("daily_column", "workout_column"):
            cols: dict[str, str] = {}
            for code, m in self.metrics.items():
                col = m.get(view)
                if not col:
                    continue
                if col in cols:
                    err.append(f"{view} {col}: used by {cols[col]} and {code}")
                cols[col] = code
                if m.get("column_type") not in COLUMN_TYPES:
                    err.append(f"metric {code}: column_type {m.get('column_type')!r}")
                if (m.get("column_type") == "STRING") != (m.get("value_type") == "text"):
                    err.append(f"metric {code}: column_type does not match value_type")
        for code, m in self.methods.items():
            if m.get("evidence") not in EVIDENCE:
                err.append(f"method {code}: evidence {m.get('evidence')!r}")
            if m.get("status") not in STATUS:
                err.append(f"method {code}: status {m.get('status')!r}")
            for p in m.get("metrics") or []:
                if not (any(fnmatchcase(c, p) for c in self.metrics) or p == "x.*"):
                    err.append(f"method {code}: pattern {p!r} matches no metric")
            if not m.get("metrics"):
                err.append(f"method {code}: no metrics")
        dims: dict[str, int] = {}
        for code, u in self.units.items():
            if u.get("canonical"):
                dims[u["dimension"]] = dims.get(u["dimension"], 0) + 1
            elif "to_canonical" not in u:
                err.append(f"unit {code}: neither canonical nor convertible")
        for d in {u["dimension"] for u in self.units.values()}:
            if dims.get(d) != 1:
                err.append(f"dimension {d}: needs exactly one canonical unit, has {dims.get(d, 0)}")
        seen: dict[tuple[str, str], str] = {}
        for code, s in self.sports.items():
            for src, names in (s.get("mappings") or {}).items():
                for n in names:
                    if (src, n) in seen:
                        err.append(f"sport {src}:{n} mapped to both {seen[src, n]} and {code}")
                    seen[src, n] = code
        for code, s in self.sources.items():
            if s.get("status") not in SOURCE_STATUS:
                err.append(f"source {code}: status {s.get('status')!r}")
            if "ai_allowed" not in s:
                err.append(f"source {code}: ai_allowed must be stated")
        for code, mod in self.modules.items():
            for p in mod.get("consumes") or []:
                if not any(fnmatchcase(c, p.get("metric", "")) for c in self.metrics):
                    err.append(f"module {code}: consumes unknown metric {p}")
        return err


def _index(items: list[dict], kind: str, errors: list[str], defaults: dict | None = None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for it in items or []:
        code = it.get("code")
        if not code:
            errors.append(f"{kind}: entry without code: {it}")
            continue
        if code in out:
            errors.append(f"{kind} {code}: defined twice")
        out[code] = {**(defaults or {}), **it}
    return out


def load(directory: Path = REGISTRY_DIR) -> Registry:
    errors: list[str] = []

    def read(name: str, key: str) -> list[dict]:
        data = yaml.safe_load((directory / f"{name}.yaml").read_text(encoding="utf-8")) or {}
        return data.get(key) or []

    return Registry(
        metrics=_index(read("metrics", "metrics"), "metric", errors,
                       {"comparable": "none", "proprietary": False, "revision": "immutable"}),
        methods=_index(read("methods", "methods"), "method", errors, {"status": "active"}),
        units=_index(read("units", "units"), "unit", errors),
        sports=_index(read("sports", "sports"), "sport", errors),
        sources=_index(read("sources", "sources"), "source", errors),
        origins=_index(read("origins", "origins"), "origin", errors),
        modules=_index(read("modules", "modules"), "module", errors),
        errors=errors,
    )


@lru_cache(maxsize=1)
def registry() -> Registry:
    return load()

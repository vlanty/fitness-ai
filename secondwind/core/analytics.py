"""Stage 3 screens without AI: personal records, month summary, period comparison.

Pure functions over bq_reader rows -> Telegram HTML. Tables are monospace (<pre>)
and kept under ~34 characters wide so they fit a phone screen.
"""

from __future__ import annotations

import html
from datetime import date, timedelta

from secondwind.core.athlete import fmt_duration, fmt_pace
from secondwind.core.i18n import t

RECORD_KEYS = {1000: "1k", 1609: "mile", 5000: "5k", 10000: "10k", 21098: "half", 42195: "marathon"}
COMPARE_SPANS = ("week", "month", "4w", "year")
# Activity types grouped for the "by sport" line.
TYPE_GROUPS = {"run": ("running",), "strength": ("strength",), "cycling": ("cycling", "biking"),
               "swim": ("swim",), "walk": ("walking", "hiking")}


def _esc(s) -> str:
    return html.escape(str(s), quote=False)


def _d(v) -> str:
    return v.strftime("%d.%m.%Y") if hasattr(v, "strftime") else str(v)


# ---------- records ----------

def render_records(lang: str, rows: list[dict]) -> str:
    if not rows:
        return t(lang, "rec.none")
    by_dist: dict[int, dict] = {}
    for r in rows:
        by_dist.setdefault(r["d"], {})[r["span"]] = r
    lines = [f"<b>{t(lang, 'rec.title')}</b>", ""]
    for d, spans in sorted(by_dist.items()):
        best = spans.get("all")
        if not best:
            continue
        name = t(lang, f"rec.d.{RECORD_KEYS.get(d, d)}")
        lines.append(f"<b>{name}</b> — {fmt_duration(round(best['secs']))} "
                     f"({fmt_pace(best['secs'] / d * 1000)}/{t(lang, 'unit.km')})")
        lines.append(f"  {_d(best['activity_date'])} · {_esc(best['name'] or '—')}")
        recent = spans.get("recent")
        if recent and round(recent["secs"]) != round(best["secs"]):
            lines.append("  " + t(lang, "rec.recent", time=fmt_duration(round(recent["secs"])),
                                  date=_d(recent["activity_date"])))
    lines += ["", t(lang, "rec.footer")]
    return "\n".join(lines)


# ---------- periods ----------

def periods(span: str, today: date) -> tuple[tuple[date, date], tuple[date, date]]:
    """(current, previous) date ranges. The current one runs to today; the previous one
    covers the same number of days, so a half-finished week is compared fairly."""
    if span == "week":
        start = today - timedelta(days=today.weekday())
        prev_start = start - timedelta(days=7)
        return (start, today), (prev_start, prev_start + (today - start))
    if span == "month":
        start = today.replace(day=1)
        prev_start = (start - timedelta(days=1)).replace(day=1)
        prev_end = min(prev_start + (today - start), start - timedelta(days=1))
        return (start, today), (prev_start, prev_end)
    if span == "4w":
        start = today - timedelta(days=27)
        return (start, today), (start - timedelta(days=28), start - timedelta(days=1))
    if span == "year":
        start = today.replace(month=1, day=1)
        prev_start = start.replace(year=start.year - 1)
        try:
            prev_end = today.replace(year=today.year - 1)
        except ValueError:  # 29 February
            prev_end = today.replace(year=today.year - 1, day=28)
        return (start, today), (prev_start, prev_end)
    raise ValueError(span)


def _num(v) -> float:
    return float(v or 0)


def _pct(cur: float, prev: float) -> str:
    if not prev:
        return "—" if not cur else "new"
    return f"{(cur - prev) / prev * 100:+.0f}%"


def _hours(secs) -> str:
    h, m = divmod(int(_num(secs)) // 60, 60)
    return f"{h}:{m:02d}"


def _pace(p: dict) -> float | None:
    km = _num(p.get("run_km"))
    return _num(p.get("run_secs")) / km if km else None


def _row(label: str, a: str, b: str, delta: str) -> str:
    return f"{label:<10}{a:>7}{b:>8}{delta:>8}"


def comparison_table(lang: str, cur: dict, prev: dict, head_a: str, head_b: str) -> str:
    pa, pb = _pace(cur), _pace(prev)
    pace_delta = f"{pa - pb:+.0f}{t(lang, 'unit.s')}" if pa and pb else "—"
    ha, hb = cur.get("run_hr"), prev.get("run_hr")
    rows = [
        _row("", head_a, head_b, "Δ"),
        _row(t(lang, "cmp.workouts"), str(cur.get("n") or 0), str(prev.get("n") or 0),
             f"{(cur.get('n') or 0) - (prev.get('n') or 0):+d}"),
        _row(t(lang, "cmp.time"), _hours(cur.get("secs")), _hours(prev.get("secs")),
             _pct(_num(cur.get("secs")), _num(prev.get("secs")))),
        _row(t(lang, "cmp.runs"), str(cur.get("runs") or 0), str(prev.get("runs") or 0),
             f"{(cur.get('runs') or 0) - (prev.get('runs') or 0):+d}"),
        _row(t(lang, "cmp.run_km"), f"{_num(cur.get('run_km')):.1f}", f"{_num(prev.get('run_km')):.1f}",
             _pct(_num(cur.get("run_km")), _num(prev.get("run_km")))),
        _row(t(lang, "cmp.pace"), fmt_pace(pa) if pa else "—", fmt_pace(pb) if pb else "—", pace_delta),
        _row(t(lang, "cmp.hr"), f"{ha:.0f}" if ha else "—", f"{hb:.0f}" if hb else "—",
             f"{ha - hb:+.0f}" if ha and hb else "—"),
        _row(t(lang, "cmp.elev"), f"{_num(cur.get('elev')):.0f}", f"{_num(prev.get('elev')):.0f}",
             _pct(_num(cur.get("elev")), _num(prev.get("elev")))),
        _row(t(lang, "cmp.load"), f"{_num(cur.get('load')):.0f}", f"{_num(prev.get('load')):.0f}",
             _pct(_num(cur.get("load")), _num(prev.get("load")))),
    ]
    return "<pre>" + _esc("\n".join(rows)) + "</pre>"


def _zones_line(lang: str, p: dict) -> str | None:
    z = [_num(p.get(f"z{i}")) for i in range(1, 6)]
    total = sum(z)
    if not total:
        return None
    return t(lang, "month.zones") + " " + " · ".join(f"Z{i + 1} {v / total * 100:.0f}%" for i, v in enumerate(z))


def _types_line(lang: str, p: dict) -> str | None:
    groups: dict[str, int] = {}
    for typ, n in (p.get("by_type") or {}).items():
        key = next((g for g, needles in TYPE_GROUPS.items() if any(x in typ for x in needles)), "other")
        groups[key] = groups.get(key, 0) + n
    if not groups:
        return None
    order = list(TYPE_GROUPS) + ["other"]
    return " · ".join(f"{t(lang, f'type.{g}')} {groups[g]}" for g in order if g in groups)


def render_month(lang: str, today: date, cur: dict, prev_full: dict) -> str:
    """This month so far next to the whole previous month."""
    start = today.replace(day=1)
    prev_start = (start - timedelta(days=1)).replace(day=1)
    lines = [f"<b>{t(lang, 'month.title', month=t(lang, f'month.name.{start.month}'), year=start.year)}</b>",
             t(lang, "month.days", days=(today - start).days + 1)]
    types = _types_line(lang, cur)
    if types:
        lines.append(types)
    lines.append(comparison_table(lang, cur, prev_full, t(lang, "month.now"),
                                  t(lang, f"month.short.{prev_start.month}")))
    zones = _zones_line(lang, cur)
    if zones:
        lines.append(zones)
    lines.append(t(lang, "month.footer"))
    return "\n".join(lines)


def render_compare(lang: str, span: str, today: date, cur: dict, prev: dict) -> str:
    (a0, a1), (b0, b1) = periods(span, today)
    lines = [f"<b>{t(lang, f'cmp.title.{span}')}</b>",
             t(lang, "cmp.ranges", a=f"{a0:%d.%m}–{a1:%d.%m}", b=f"{b0:%d.%m}–{b1:%d.%m}"),
             comparison_table(lang, cur, prev, t(lang, "cmp.now"), t(lang, "cmp.before"))]
    zones = _zones_line(lang, cur)
    if zones:
        lines.append(zones)
    return "\n".join(lines)


# ---------- context for Claude (English) ----------

def records_note(rows: list[dict]) -> str:
    best = [r for r in rows if r["span"] == "all"]
    if not best:
        return "no records yet"
    names = {1000: "1 km", 1609: "1 mile", 5000: "5 km", 10000: "10 km", 21098: "half marathon", 42195: "marathon"}
    return "; ".join(f"{names.get(r['d'], r['d'])} {fmt_duration(round(r['secs']))} ({r['activity_date']})" for r in best)


def month_note(cur: dict, prev: dict) -> str:
    def one(p):
        pace = _pace(p)
        return (f"{p.get('n') or 0} workouts, {_hours(p.get('secs'))} h, {p.get('runs') or 0} runs "
                f"{_num(p.get('run_km')):.1f} km" + (f" at {fmt_pace(pace)}/km" if pace else "")
                + (f", run HR {p['run_hr']:.0f}" if p.get("run_hr") else "") + f", load {_num(p.get('load')):.0f}")
    return f"this month so far: {one(cur)}; previous month (whole): {one(prev)}"

"""PNG charts for Telegram (matplotlib, no display).

Style: navy surface matching the bot avatar; at most two series per chart in
validated slots (blue, orange: CVD ΔE 26.8 / normal ΔE 31.8 on #0f1f3a, both
>= 3:1 contrast); one y-axis per chart; recessive grid; legend for two series.
"""

from __future__ import annotations

import io
from datetime import date, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from secondwind.core.i18n import t  # noqa: E402

SURFACE = "#0f1f3a"
TEXT = "#f0efec"
TEXT_2 = "#c3c2b7"
GRID = "#2b3b58"
SERIES_1 = "#3987e5"   # blue
SERIES_2 = "#d95926"   # orange
BAND = "#8a93a6"       # neutral, for ranges

CHARTS = ("volume", "load", "hrv", "rhr", "efficiency")


def _fig(title: str, subtitle: str):
    fig, ax = plt.subplots(figsize=(8, 4.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_2, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    fig.text(0.04, 0.94, title, color=TEXT, fontsize=14, fontweight="bold", va="top")
    fig.text(0.04, 0.875, subtitle, color=TEXT_2, fontsize=9.5, va="top")
    fig.subplots_adjust(left=0.08, right=0.95, top=0.78, bottom=0.12)
    return fig, ax


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buf.getvalue()


def _dates_axis(ax):
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=7))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))


def _legend(ax):
    leg = ax.legend(loc="upper left", frameon=False, fontsize=9, labelcolor=TEXT_2, ncol=2,
                    bbox_to_anchor=(0, 1.12))
    return leg


def _end_label(ax, x, y, text):
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", color=TEXT, fontsize=9, va="center")


def _empty(lang: str, title: str) -> bytes:
    fig, ax = _fig(title, t(lang, "chart.no_data"))
    ax.set_xticks([])
    ax.set_yticks([])
    return _png(fig)


def volume(lang: str, rows: list[dict]) -> bytes:
    title = t(lang, "chart.volume.title")
    if not rows or not any(r["km"] for r in rows):
        return _empty(lang, title)
    weeks = [r["week"] for r in rows]
    km = [float(r["km"] or 0) for r in rows]
    avg = sum(km[:-1]) / max(len(km) - 1, 1)
    fig, ax = _fig(title, t(lang, "chart.volume.sub", n=len(rows), avg=f"{avg:.1f}"))
    colors = [SERIES_1] * (len(km) - 1) + [TEXT_2]          # current (unfinished) week muted
    ax.bar(weeks, km, width=5, color=colors, edgecolor=SURFACE, linewidth=2)
    ax.axhline(avg, color=BAND, linewidth=1, linestyle=(0, (3, 3)))
    ax.annotate(f"{km[-2]:.0f}", (weeks[-2], km[-2]), xytext=(0, 4), textcoords="offset points",
                ha="center", color=TEXT, fontsize=9)
    ax.set_ylabel(t(lang, "unit.km"), color=TEXT_2, fontsize=9)
    _dates_axis(ax)
    return _png(fig)


def load(lang: str, rows: list[dict]) -> bytes:
    title = t(lang, "chart.load.title")
    rows = [r for r in rows if r.get("acute_load") is not None]
    if not rows:
        return _empty(lang, title)
    d = [r["date"] for r in rows]
    acute = [float(r["acute_load"] or 0) for r in rows]
    chronic = [float(r["chronic_load"] or 0) for r in rows]
    fig, ax = _fig(title, t(lang, "chart.load.sub"))
    ax.plot(d, chronic, color=SERIES_2, linewidth=2, label=t(lang, "chart.load.chronic"))
    ax.plot(d, acute, color=SERIES_1, linewidth=2, label=t(lang, "chart.load.acute"))
    _end_label(ax, d[-1], acute[-1], f"{acute[-1]:.0f}")
    _end_label(ax, d[-1], chronic[-1], f"{chronic[-1]:.0f}")
    _legend(ax)
    _dates_axis(ax)
    return _png(fig)


def hrv(lang: str, rows: list[dict]) -> bytes:
    title = t(lang, "chart.hrv.title")
    rows = [r for r in rows if r.get("hrv_last_night")][-60:]
    if not rows:
        return _empty(lang, title)
    d = [r["date"] for r in rows]
    v = [r["hrv_last_night"] for r in rows]
    fig, ax = _fig(title, t(lang, "chart.hrv.sub", days=len(rows)))
    band = [(r["date"], r["hrv_baseline_low"], r["hrv_baseline_high"]) for r in rows
            if r.get("hrv_baseline_low") and r.get("hrv_baseline_high")]
    if band:
        ax.fill_between([b[0] for b in band], [b[1] for b in band], [b[2] for b in band],
                        color=BAND, alpha=0.25, linewidth=0, label=t(lang, "chart.hrv.band"))
    ax.plot(d, v, color=SERIES_1, linewidth=2, marker="o", markersize=3.5, label=t(lang, "chart.hrv.night"))
    _end_label(ax, d[-1], v[-1], str(v[-1]))
    _legend(ax)
    ax.set_ylabel(t(lang, "unit.ms"), color=TEXT_2, fontsize=9)
    _dates_axis(ax)
    return _png(fig)


def _rolling(values: list[float], n: int) -> list[float]:
    out = []
    for i in range(len(values)):
        window = values[max(0, i - n + 1): i + 1]
        out.append(sum(window) / len(window))
    return out


def rhr(lang: str, rows: list[dict]) -> bytes:
    title = t(lang, "chart.rhr.title")
    rows = [r for r in rows if r.get("resting_hr")]
    if not rows:
        return _empty(lang, title)
    d = [r["date"] for r in rows]
    v = [float(r["resting_hr"]) for r in rows]
    avg7 = _rolling(v, 7)
    fig, ax = _fig(title, t(lang, "chart.rhr.sub", days=len(rows)))
    ax.plot(d, v, color=BAND, linewidth=1, alpha=0.7, label=t(lang, "chart.rhr.daily"))
    ax.plot(d, avg7, color=SERIES_1, linewidth=2, label=t(lang, "chart.rhr.avg7"))
    _end_label(ax, d[-1], avg7[-1], f"{avg7[-1]:.0f}")
    _legend(ax)
    ax.set_ylabel(t(lang, "unit.bpm"), color=TEXT_2, fontsize=9)
    _dates_axis(ax)
    return _png(fig)


def efficiency(lang: str, rows: list[dict]) -> bytes:
    title = t(lang, "chart.eff.title")
    rows = [r for r in rows if r.get("m_per_beat")]
    if len(rows) < 3:
        return _empty(lang, title)
    d = [r["date"] for r in rows]
    v = [float(r["m_per_beat"]) for r in rows]
    trend = _rolling(v, 6)
    fig, ax = _fig(title, t(lang, "chart.eff.sub"))
    ax.scatter(d, v, s=22, color=SERIES_1, edgecolors=SURFACE, linewidths=1, label=t(lang, "chart.eff.run"), zorder=3)
    ax.plot(d, trend, color=SERIES_2, linewidth=2, label=t(lang, "chart.eff.trend"), zorder=4)
    _end_label(ax, d[-1], trend[-1], f"{trend[-1]:.2f}")
    _legend(ax)
    ax.set_ylabel(t(lang, "chart.eff.unit"), color=TEXT_2, fontsize=9)
    _dates_axis(ax)
    return _png(fig)


def render(name: str, lang: str, bq) -> bytes:
    if name == "volume":
        return volume(lang, bq.get_weekly_run_km(16))
    if name == "load":
        return load(lang, bq.get_load_series(120))
    if name == "hrv":
        return hrv(lang, bq.get_health_series(60))
    if name == "rhr":
        return rhr(lang, bq.get_health_series(90))
    if name == "efficiency":
        return efficiency(lang, bq.get_run_efficiency(180))
    raise ValueError(name)

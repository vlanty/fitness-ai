"""Is each data source up to date?"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from secondwind.core.i18n import t

STALE_AFTER = timedelta(hours=26)


def stale_warnings(status_rows: list[dict], enabled: list[str], lang: str = "en",
                   now: datetime | None = None) -> list[str]:
    """One line per enabled source that has not synced successfully for > STALE_AFTER."""
    now = now or datetime.now(timezone.utc)
    by_source = {r["source"]: r for r in status_rows}
    out = []
    for name in enabled:
        r = by_source.get(name)
        last = r.get("last_success_at") if r else None
        if last is None:
            out.append(t(lang, "stale.never", source=name))
        elif now - last > STALE_AFTER:
            status = f" ({r['last_status']})" if r.get("last_status") not in (None, "ok") else ""
            out.append(t(lang, "stale.old", source=name, when=f"{last:%d.%m %H:%M}", status=status))
    return out

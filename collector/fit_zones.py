"""Heart-rate zones the watch used for an activity, read from its .FIT file.

Garmin writes a `time_in_zone` message per session/lap with the zone boundaries,
how they were computed (percent of max HR, of HR reserve or of lactate threshold),
and the threshold / max / resting HR at the time. Using these makes the bot's
zones identical to the watch's and to Garmin's per-activity time in zone.

hr_zone_high_boundary = [z0_top, z1_top, ..., z5_top]: zone N spans
boundary[N-1]..boundary[N]; zone 0 is below zone 1.
"""

from __future__ import annotations

import io
import logging
import zipfile

logger = logging.getLogger(__name__)

ZONE_COLUMNS = ("hr_zone_calc", "hr_zone_bounds", "lthr", "fit_max_hr", "fit_resting_hr")


def _fit_bytes(blob: bytes) -> bytes | None:
    """Garmin sends a ZIP with one .fit inside; accept a bare .fit too."""
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fit")]
            return z.read(names[0]) if names else None
    return blob


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def extract_zones(blob: bytes) -> dict:
    """Zone columns for the activity, or {} if the file has none / can't be read."""
    try:
        from garmin_fit_sdk import Decoder, Stream

        data = _fit_bytes(blob)
        if not data:
            return {}
        msgs, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
        if errors:
            logger.info("fit: decode warnings: %s", errors[:2])
    except Exception as e:  # noqa: BLE001 - a broken file must never fail the sync
        logger.warning("fit: cannot read zones: %s", e)
        return {}

    tiz = [m for m in msgs.get("time_in_zone_mesgs", []) if m.get("hr_zone_high_boundary")]
    session = next((m for m in tiz if m.get("reference_mesg") == "session"), tiz[0] if tiz else None)
    target = (msgs.get("zones_target_mesgs") or [{}])[0]
    if not session:
        return {}
    bounds = [b for b in (_int(x) for x in session["hr_zone_high_boundary"]) if b]
    if len(bounds) < 3 or bounds != sorted(bounds):
        return {}
    calc = session.get("hr_calc_type") or target.get("hr_calc_type")
    return {
        "hr_zone_calc": str(calc) if calc is not None else None,
        "hr_zone_bounds": ",".join(map(str, bounds)),
        "lthr": _int(session.get("threshold_heart_rate") or target.get("threshold_heart_rate")),
        "fit_max_hr": _int(session.get("max_heart_rate") or target.get("max_heart_rate")),
        "fit_resting_hr": _int(session.get("resting_heart_rate")),
    }

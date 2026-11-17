"""Strava source (OAuth refresh token). Activities only, no health data."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import requests

from .base import Batch, Source, to_json

logger = logging.getLogger(__name__)

STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


def _int(v):
    return int(round(v)) if v is not None else None


def parse_activity(raw: dict) -> dict:
    start_dt = datetime.fromisoformat(raw["start_date"].replace("Z", "+00:00"))
    dist = raw.get("distance", 0) or 0
    duration = raw.get("moving_time", 0) or 0
    pace = (duration / (dist / 1000)) if dist > 0 else None
    return {
        "activity_id": str(raw["id"]),
        "source": "strava",
        "activity_date": start_dt.date().isoformat(),
        "start_time": start_dt.isoformat(),
        "name": raw.get("name"),
        "type": raw.get("type"),
        "distance_meters": float(dist),
        "duration_seconds": _int(duration),
        "elevation_gain": float(raw.get("total_elevation_gain") or 0),
        "avg_heart_rate": _int(raw.get("average_heartrate")),
        "max_heart_rate": _int(raw.get("max_heartrate")),
        "avg_pace_sec_per_km": pace,
        "calories": _int(raw.get("calories")),
        "raw_json": to_json(raw),
    }


class StravaSource(Source):
    name = "strava"
    required_env = ("STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET", "STRAVA_REFRESH_TOKEN")
    lookback_days = 1
    max_window_days = 3650  # one paginated listing covers any range

    def _token(self) -> str:
        resp = requests.post(STRAVA_TOKEN_URL, data={
            "client_id": self.env["STRAVA_CLIENT_ID"],
            "client_secret": self.env["STRAVA_CLIENT_SECRET"],
            "refresh_token": self.env["STRAVA_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["access_token"]

    def fetch(self, start: date, end: date, known_activity_ids: set[str]) -> Batch:
        after_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
        before_ts = int(datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
        headers = {"Authorization": "Bearer " + self._token()}
        raw, page = [], 1
        while True:
            resp = requests.get(
                STRAVA_API_BASE + "/athlete/activities",
                headers=headers,
                params={"after": after_ts, "before": before_ts, "per_page": 100, "page": page},
                timeout=30,
            )
            resp.raise_for_status()
            chunk = resp.json()
            if not chunk:
                break
            raw.extend(chunk)
            page += 1
        logger.info("strava: получено %d активностей", len(raw))
        return Batch(activities=[parse_activity(a) for a in raw])

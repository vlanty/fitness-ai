from datetime import date, datetime, timedelta, timezone

from secondwind.core.freshness import stale_warnings
from secondwind.apps.bot import handlers
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def test_stale_warnings():
    rows = [{"source": "garmin", "last_success_at": NOW - timedelta(hours=2), "last_status": "ok"},
            {"source": "strava", "last_success_at": NOW - timedelta(days=3), "last_status": "failed"}]
    assert stale_warnings(rows, ["garmin"], "ru", NOW) == []
    w = stale_warnings(rows, ["garmin", "strava", "polar"], "ru", NOW)
    assert len(w) == 2 and "strava" in w[0] and "(failed)" in w[0] and "ни разу" in w[1]


def test_format_activity_escapes_markdown_and_shows_source():
    text = handlers._fmt_activity({"name": "run_with*stars", "type": "running", "source": "garmin",
                                   "activity_date": date(2026, 11, 16), "distance_meters": 10000,
                                   "duration_seconds": 3000, "avg_pace_sec_per_km": 300, "aerobic_te": 3.4}, "ru")
    assert "run\\_with\\*stars" in text and "Garmin" in text and "5:00/км" in text and "TE аэроб 3.4" in text


def test_format_health():
    text = handlers._fmt_health({"date": date(2026, 11, 16), "sleep_seconds": 27000, "deep_sleep_seconds": 5400,
                                 "rem_sleep_seconds": 6000, "sleep_score": 82, "hrv_last_night": 50,
                                 "resting_hr": 47, "body_battery_high": 85, "body_battery_low": 20}, "ru")
    assert "сон 7ч 30м" in text and "HRV 50" in text and "пульс покоя 47" in text and "BB 20→85" in text


class Chat:
    def __init__(self, cid):
        self.id = cid


class Upd:
    def __init__(self, cid):
        self.effective_chat = Chat(cid)


def test_only_owner_chat_allowed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert handlers._allowed(Upd(42))
    assert not handlers._allowed(Upd(7))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    assert not handlers._allowed(Upd(42))

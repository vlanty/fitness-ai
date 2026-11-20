from datetime import date, datetime, timedelta, timezone

from secondwind.core.freshness import stale_warnings
from secondwind.apps.bot import handlers
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def test_stale_warnings():
    rows = [{"source": "garmin", "last_success_at": NOW - timedelta(hours=2), "last_status": "ok"},
            {"source": "polar", "last_success_at": NOW - timedelta(days=3), "last_status": "failed"}]
    assert stale_warnings(rows, ["garmin"], "ru", NOW) == []
    w = stale_warnings(rows, ["garmin", "polar", "coros"], "ru", NOW)
    assert len(w) == 2 and "polar" in w[0] and "(failed)" in w[0] and "ни разу" in w[1]


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
    monkeypatch.setenv("OWNER_USER_ID", "u-1")
    monkeypatch.delenv("TENANCY", raising=False)
    assert handlers._allowed(Upd(42)) and handlers._uid(Upd(42)) == "u-1"
    assert not handlers._allowed(Upd(7))
    monkeypatch.setenv("OWNER_USER_ID", "")       # no user yet: nobody may use the bot
    assert not handlers._allowed(Upd(42))
    monkeypatch.setenv("OWNER_USER_ID", "u-1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    assert not handlers._allowed(Upd(42))


def test_multi_tenancy_is_not_enabled_by_accident(monkeypatch):
    import pytest
    from secondwind.apps.bot import tenancy
    monkeypatch.setenv("TENANCY", "multi")
    with pytest.raises(NotImplementedError):
        tenancy.user_for_chat(42)


def test_memory_store_is_keyed_by_user_and_links_identities():
    from secondwind.adapters.state.memory import MemoryStore
    st = MemoryStore()
    st.update_profile("u-1", {"weight": 70})
    st.add_usage("u-1", "2026-11", 10, 5, 0.01)
    st.link_identity("telegram:42", "u-1")
    assert st.get_user("u-1")["profile"] == {"weight": 70} and st.get_user("u-2") == {}
    assert st.get_usage("u-1", "2026-11")["calls"] == 1 and st.get_identity("telegram:42") == "u-1"


def test_athlete_inputs_are_read_for_the_chat_user(monkeypatch):
    calls = []

    class Reader:
        def get_athlete_inputs(self):
            calls.append("read")
            return {"resting_hr_7d": 47}

    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OWNER_USER_ID", "u-1")
    monkeypatch.setattr(handlers, "_bq", lambda update: Reader())
    assert handlers._athlete_inputs(Upd(42)) == {"resting_hr_7d": 47} and calls == ["read"]

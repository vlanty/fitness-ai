"""Filter 2.1 (architecture): length, repeats, model-call rate per user. Written before the code."""

from datetime import datetime, timedelta, timezone

from secondwind.core.guard import is_repeat, model_allowed, record_message, record_model_call
from secondwind.core.router import MAX_CHARS, route

NOW = datetime(2026, 11, 20, 19, 0, tzinfo=timezone.utc)


def test_too_long_is_answered_without_the_model():
    assert route("a" * (MAX_CHARS + 1), "en").key == "filter.too_long"
    assert route("сколько я пробежал " + "очень " * 10, "ru").key != "filter.too_long"


def test_same_text_twice_within_two_minutes_is_a_repeat():
    g = record_message({}, "Сколько я пробежал?", NOW)
    assert is_repeat(g, "сколько я  пробежал ?", NOW + timedelta(seconds=30))     # case/space-insensitive
    assert not is_repeat(g, "сколько я пробежал?", NOW + timedelta(minutes=3))
    assert not is_repeat(g, "а вчера?", NOW + timedelta(seconds=5))
    assert not is_repeat({}, "anything", NOW)


def test_model_calls_are_limited_per_hour():
    g = {}
    for i in range(3):
        assert model_allowed(g, NOW + timedelta(minutes=i), limit=3)
        g = record_model_call(g, NOW + timedelta(minutes=i))
    assert not model_allowed(g, NOW + timedelta(minutes=5), limit=3)
    assert model_allowed(g, NOW + timedelta(minutes=61), limit=3)               # the oldest call has expired
    assert len(record_model_call(g, NOW + timedelta(hours=2))["model_calls"]) == 1   # old entries are dropped


def test_bot_applies_repeat_and_rate_limit(monkeypatch):
    from secondwind.adapters.state.memory import MemoryStore
    from secondwind.apps.bot import handlers

    class Chat:
        id = 42

    class Upd:
        effective_chat = Chat()
        effective_user = None

    store = MemoryStore()
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OWNER_USER_ID", "u-1")
    monkeypatch.setattr(handlers, "_store", lambda: store)
    monkeypatch.setattr(handlers, "MODEL_CALLS_PER_HOUR", 2)

    def go(text, minutes):
        return handlers._guarded_route(Upd(), store.get_user("u-1"), text, NOW + timedelta(minutes=minutes)).key

    assert go("почему у меня низкий HRV?", 0) == "model"
    assert go("почему у меня низкий HRV?", 1) == "filter.repeat"
    assert go("болит в груди", 2) == "filter.red_flag"
    assert go("болит в груди", 2.5) == "filter.red_flag"          # emergencies are answered every time
    assert go("а почему упал сон?", 3) == "model"
    assert go("и что с пульсом, почему вырос?", 4) == "filter.rate_limited"
    assert go("сколько я пробежал на этой неделе", 5) == "fact.run_distance"   # rules still work
    assert go("почему так?", 70) == "model"                       # an hour later the limit is free again

"""Answers given without the AI model."""

from datetime import date

import pytest

from secondwind.apps.bot import handlers
from secondwind.core.router import route

TODAY = date.today()


class Chat:
    id = 42


class Upd:
    effective_chat = Chat()


class Reader:
    def get_health_last_days(self, days):
        return [{"date": TODAY, "resting_hr": 47, "hrv_last_night": 50, "hrv_baseline_low": 45, "hrv_baseline_high": 60,
                 "hrv_status": "BALANCED", "sleep_seconds": 27000, "deep_sleep_seconds": 5400, "rem_sleep_seconds": 6000,
                 "sleep_score": 82, "steps": 9000, "body_battery_low": 20, "body_battery_high": 85, "stress_avg": 25}]

    def get_period(self, start, end):
        return {"runs": 3, "run_km": 24.04}

    def get_last_activity(self):
        return None

    def get_load_last_days(self, days):
        return [{"date": TODAY, "vo2max": 52.34}]

    def get_athlete_inputs(self):
        return {}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    monkeypatch.setenv("OWNER_USER_ID", "u-1")
    monkeypatch.setattr(handlers, "_bq", lambda update: Reader())


def answer(text, lang="ru", user=None):
    r = route(text, lang, TODAY)
    return handlers._fast_answer(Upd(), r, user or {})


def test_filters_answer_with_fixed_texts():
    assert "112" in answer("болит в груди")[0]
    assert answer("спасибо")[0].startswith("Пожалуйста")
    assert "Russian and English" in answer("你好")[0]


def test_metric_values_from_the_data():
    assert answer("какой пульс покоя сегодня")[0] == f"❤️ Пульс покоя за {TODAY:%d.%m}: <b>47</b> уд/мин"
    assert "50 мс" in answer("какой у меня hrv сегодня")[0] and "норма 45–60" in answer("какой у меня hrv сегодня")[0]
    assert "7ч 30м" in answer("сколько я спал сегодня")[0]
    assert "Данных за" in answer("какой пульс покоя вчера")[0]
    assert "52.3" in answer("what is my vo2max", "en")[0]


def test_run_distance_and_pace():
    assert "<b>24.0 км</b>, пробежек: 3" in answer("сколько я пробежал на этой неделе")[0]
    assert answer("10 км за 50 минут")[0] == "⏱ 10 км за 50:00 = темп <b>5:00/км</b>"
    assert answer("темп 5:30 на 21.1 км")[0] == "⏱ 21.1 км в темпе 5:30/км = <b>1:56:03</b>"


def test_zones_without_data_ask_for_the_profile():
    text, kw = answer("какие у меня пульсовые зоны")
    assert kw == {"parse_mode": "HTML"} and text

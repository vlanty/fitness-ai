"""Golden cases for the filter and router, plus parameter extraction."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from secondwind.core.router import detect_lang, period_dates, route

GOLDEN = yaml.safe_load((Path(__file__).resolve().parent.parent / "secondwind" / "contracts" / "golden" /
                         "router.yaml").read_text(encoding="utf-8"))["cases"]
TODAY = date(2026, 11, 20)   # a Friday


@pytest.mark.parametrize("case", GOLDEN, ids=[c["text"] for c in GOLDEN])
def test_golden(case):
    r = route(case["text"], "ru", TODAY)
    assert r.key == case["expect"] and r.lang == case["lang"], (r.kind, r.key, r.params)


def test_languages():
    assert detect_lang("привет") == "ru" and detect_lang("hi") == "en" and detect_lang("你好") is None
    assert route("你好", "en").key == "filter.unsupported_language"
    assert route("   ", "ru").key == "filter.empty"


def test_periods_and_metric_params():
    r = route("сколько я пробежал в прошлом месяце", "ru", TODAY)
    assert r.params["period"] == "last_month" and r.params["start"] == date(2026, 10, 1) and r.params["end"] == date(2026, 10, 31)
    assert route("сколько км пробежал", "ru", TODAY).params["start"] == date(2026, 11, 16)   # default: this week
    r = route("какой пульс покоя вчера", "ru", TODAY)
    assert r.params == {"metric": "resting_hr", "period": "yesterday", "date": date(2026, 11, 19)}
    assert route("сколько шагов за месяц", "ru", TODAY).key == "model"       # a trend over a period = analysis
    assert period_dates("last_week", TODAY) == (date(2026, 11, 9), date(2026, 11, 15))


def test_pace_params():
    assert route("10 км за 50 минут", "ru").params == {"distance_km": 10.0, "total_s": 3000}
    assert route("21,1 км за 1:45:00", "ru").params == {"distance_km": 21.1, "total_s": 6300}
    assert route("темп 5:30 на 10 км", "ru").params == {"distance_km": 10.0, "pace_s": 330}
    assert route("half marathon in 1 h 50 min pace", "en").key == "model"    # no km number and no 'pace X:XX'
    assert route("marathon pace 4:15", "en").params == {"distance_km": 42.195, "pace_s": 255}

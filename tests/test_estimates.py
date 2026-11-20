from datetime import date

import estimates as e
import handlers


def day(**kw):
    base = {"sleep_score": 80, "stress_avg": 25, "hrv_last_night": 55, "hrv_baseline_low": 50,
            "hrv_baseline_high": 65, "hrv_status": "BALANCED"}
    return {**base, **kw}


LOAD_OK = {"date": date(2026, 11, 17), "acute_load": 600, "chronic_load": 550, "load_ratio": 1.09,
           "load_ratio_zone": "optimal", "vo2max": 52.0, "vo2max_28d_ago": 50.5,
           "last_activity_date": date(2026, 11, 16)}


def test_scores():
    assert e.hrv_score(55, 50, 65) == 100
    assert e.hrv_score(45, 50, 65) == 60          # 10% below range
    assert e.hrv_score(None, 50, 65) is None
    assert e.load_score(1.0) == 100 and e.load_score(2.0) == 0 and round(e.load_score(1.7)) == 50
    assert e.stress_score(20) == 100 and e.stress_score(50) == 50


def test_readiness_good_day_is_high_and_labelled():
    est = e.readiness([day(), day(), day()], LOAD_OK, lang="ru")
    assert est.score >= 90 and est.value in ("высокая", "отличная")
    text = est.text("Готовность")
    assert "не Garmin" in text and "recovery time" in text


def test_readiness_bad_night_and_overload_drops():
    est = e.readiness([day(), day(), day(sleep_score=40, hrv_last_night=38, stress_avg=60)],
                      {**LOAD_OK, "load_ratio": 1.9, "load_ratio_zone": "high"})
    assert est.score < 50


def test_readiness_needs_three_factors():
    assert e.readiness([{"sleep_score": 80}], None, lang="ru").value == "мало данных"
    assert e.readiness([], None, lang="ru").value == "нет данных"


def test_training_status_rules():
    assert e.training_status(LOAD_OK, [day()], lang="ru").value.startswith("продуктивно")
    assert e.training_status({**LOAD_OK, "load_ratio": 1.7, "load_ratio_zone": "high"}, [day()], lang="ru").value.startswith("перегрузка")
    assert e.training_status(LOAD_OK, [day(hrv_status="LOW")], lang="ru").value.startswith("напряжение")
    assert e.training_status({**LOAD_OK, "vo2max": 49.0}, [day()], lang="ru").value.startswith("непродуктивно")
    assert e.training_status({**LOAD_OK, "load_ratio": 0.6, "load_ratio_zone": "low", "vo2max_28d_ago": 52.0}, [day()], lang="ru").value.startswith("восстановление")
    assert e.training_status({**LOAD_OK, "last_activity_date": date(2026, 11, 5)}, [day()], lang="ru").value.startswith("детренированность")
    assert e.training_status(None, [], lang="ru").value == "нет данных"
    assert "не Garmin" in e.training_status(LOAD_OK, [day()], lang="ru").text("Статус")


def test_bot_formats_load_and_hrv_norm():
    text = handlers._fmt_load({**LOAD_OK, "focus_low_aerobic_28d": 600, "focus_high_aerobic_28d": 300,
                               "focus_anaerobic_28d": 100}, "ru")
    assert "1.09 (optimal" in text and "низкая аэробная 60%" in text and "+1.5 за 28 дн" in text
    assert "норма 50–65" in handlers._fmt_health({"date": date(2026, 11, 17), **day()}, "ru")

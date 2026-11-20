"""The registries are consistent, and the codes and conversions behave as documented."""

import pytest

from secondwind.core.registry import UnknownCode, load, registry

R = registry()

# Garmin activity types present in the live data (2026-11-20): all must map to a sport.
GARMIN_TYPES = ["strength_training", "running", "yoga", "walking", "elliptical", "pilates", "treadmill_running",
                "indoor_cardio", "open_water_swimming", "hiking", "cycling", "indoor_cycling", "mountaineering",
                "lap_swimming", "boxing", "dance", "obstacle_run", "boating_v2", "trail_running", "rowing_v2",
                "e_sport", "breathwork"]


def test_registries_are_consistent():
    assert R.validate() == []


def test_known_garmin_types_have_a_sport():
    assert R.sport_for("garmin", "treadmill_running") == "run"
    assert R.sport_for("garmin", "lap_swimming") == "swim"
    assert R.sport_for("garmin", "something_new") == "other"
    unmapped = [t for t in GARMIN_TYPES if not any(t in (s.get("mappings") or {}).get("garmin", [])
                                                   for s in R.sports.values())]
    assert unmapped == []


def test_unmapped_vendor_fields_are_accepted_as_x():
    meta = R.metric("x.garmin.someNewField")
    assert meta["value_type"] == "json" and meta["proprietary"]
    R.check_observation("x.garmin.someNewField", "vendor_native", None)
    with pytest.raises(UnknownCode):
        R.metric("hrv.something_undefined")


def test_check_observation_enforces_method_and_unit():
    R.check_observation("hrv.night", "rmssd_sleep_night_mean", "ms")
    with pytest.raises(UnknownCode):
        R.check_observation("hrv.night", "lowest_30min_mean_24h", "ms")   # method not for this metric
    with pytest.raises(UnknownCode):
        R.check_observation("hrv.night", "rmssd_sleep_night_mean", "s")   # wrong unit


def test_conversions():
    assert R.to_canonical(5, "km", "workout.distance") == (5000, "m")
    v, u = R.to_canonical(98.6, "[degF]", "temp.skin")
    assert u == "Cel" and round(v, 1) == 37.0
    v, _ = R.to_canonical(-2.79, "[degF]", "temp.skin_deviation")       # a delta: no offset
    assert round(v, 2) == -1.55
    with pytest.raises(UnknownCode):
        R.to_canonical(5.5, "mmol/L", "workout.distance")                # glucose units only for glucose.*


def test_vendor_scores_are_proprietary_and_strava_is_not_for_ai():
    assert all(m["proprietary"] for c, m in R.metrics.items() if c.startswith("vendor_score."))
    assert R.sources["strava"]["status"] == "unsupported" and R.sources["strava"]["ai_allowed"] is False


def test_validation_catches_mistakes(tmp_path):
    import shutil
    from secondwind.core.registry import REGISTRY_DIR
    for f in REGISTRY_DIR.glob("*.yaml"):
        shutil.copy(f, tmp_path / f.name)
    (tmp_path / "methods.yaml").write_text(
        (tmp_path / "methods.yaml").read_text() + '  - {code: broken, metrics: ["nope.*"], evidence: maybe}\n')
    errors = load(tmp_path).validate()
    assert any("pattern 'nope.*' matches no metric" in e for e in errors)
    assert any("evidence 'maybe'" in e for e in errors)

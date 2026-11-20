from datetime import date, timedelta

from secondwind.apps.collector import main as collector
from secondwind.adapters.warehouse.bigquery_writer import dedupe, merge_sql
from secondwind.apps.collector.sources import REGISTRY
from secondwind.apps.collector.sources.base import Batch, PartialFetch, Source


def test_merge_sql_composite_key_and_coalesce():
    sql = merge_sql("p.d.activities", "p.d._stage", ["source", "activity_id", "name", "raw_file"], ("source", "activity_id"))
    assert "ON T.source = S.source AND T.activity_id = S.activity_id" in sql
    assert "T.raw_file = COALESCE(S.raw_file, T.raw_file)" in sql
    assert "T.source =" not in sql.split("UPDATE SET")[1].split("WHEN NOT")[0]


def test_dedupe_last_wins():
    rows = [{"source": "garmin", "date": "d", "v": 1}, {"source": "garmin", "date": "d", "v": 2}]
    assert dedupe(rows, ("source", "date")) == [{"source": "garmin", "date": "d", "v": 2}]


def test_windows_cover_range_without_gaps():
    ws = list(collector.windows(date(2026, 1, 1), date(2026, 3, 1), 28))
    assert ws[0][0] == date(2026, 1, 1) and ws[-1][1] == date(2026, 3, 1)
    for (a_s, a_e), (b_s, _) in zip(ws, ws[1:]):
        assert b_s == a_e + timedelta(days=1)
        assert (a_e - a_s).days == 27


class FakeBQ:
    def __init__(self):
        self.writes = []

    def write(self, model):
        self.writes.append(model)
        return {"sessions": len(model.sessions)}

    def stored_file_ids(self, source, start):
        return set()


class FakeState:
    def __init__(self, cursor=None):
        self._cursor, self.records = cursor, []

    def cursor(self, source):
        return self._cursor

    def record(self, source, cursor, status, error=None):
        self.records.append((source, cursor, status))


class Recorder(Source):
    name = "fake"
    lookback_days = 3
    max_window_days = 28
    seen = []
    fail = None

    def fetch(self, start, end, known):
        Recorder.seen.append((start, end))
        if Recorder.fail:
            raise Recorder.fail
        return Batch(activities=[{"source": "fake", "activity_id": str(start)}])

    def to_model(self, user_id, batch, store):
        from secondwind.core.observations import ModelBatch
        return ModelBatch(sessions=[{"session_id": f"fake:{a['activity_id']}"} for a in batch.activities],
                          observations=[{"date": h["date"]} for h in batch.health_daily])


def setup_function():
    Recorder.seen, Recorder.fail = [], None
    REGISTRY["fake"] = Recorder


def test_incremental_run_uses_cursor_minus_lookback_and_advances():
    bq, st = FakeBQ(), FakeState(cursor=date.today() - timedelta(days=1))
    assert collector.run_source("fake", {}, bq, st, None, "u-1")
    assert Recorder.seen == [(date.today() - timedelta(days=4), date.today())]
    assert st.records[-1] == ("fake", date.today(), "ok")


def test_first_run_loads_first_sync_days():
    collector.run_source("fake", {"FIRST_SYNC_DAYS": "10"}, FakeBQ(), FakeState(), None, "u-1")
    assert Recorder.seen[0][0] == date.today() - timedelta(days=10)


def test_failure_does_not_move_cursor_and_returns_false():
    Recorder.fail = RuntimeError("api down")
    st = FakeState(cursor=date(2026, 1, 1))
    assert not collector.run_source("fake", {}, FakeBQ(), st, None, "u-1")
    assert st.records == [("fake", None, "failed")]


def test_partial_saves_what_arrived():
    Recorder.fail = PartialFetch(Batch(health_daily=[{"source": "fake", "date": "2026-01-01"}]), ["hrv: 500"])
    bq, st = FakeBQ(), FakeState(cursor=date.today())
    assert not collector.run_source("fake", {}, bq, st, None, "u-1")
    assert any(m.observations == [{"date": "2026-01-01"}] for m in bq.writes)
    assert st.records[-1][2] == "partial"


def test_missing_secret_fails_source_only():
    st = FakeState()
    assert not collector.run_source("garmin", {}, FakeBQ(), st, None, "u-1")
    assert st.records[-1][2] == "failed"


def test_main_exit_code_reflects_failures(monkeypatch):
    monkeypatch.setattr(collector, "BigQueryModelWriter", lambda p, d, u: type("W", (), {"client": None, "table": lambda s, n: n})())
    monkeypatch.setattr(collector, "IngestState", lambda c, t, u: None)
    monkeypatch.setattr(collector, "run_source", lambda n, *a: n == "fake")
    monkeypatch.setenv("GCP_PROJECT", "p"); monkeypatch.setenv("BQ_DATASET", "d")
    monkeypatch.setenv("OWNER_USER_ID", "u-1")
    monkeypatch.setenv("DATA_SOURCES", "fake")
    assert collector.main() == 0
    monkeypatch.setenv("DATA_SOURCES", "fake,garmin")
    assert collector.main() == 1


def test_main_needs_owner_user_id(monkeypatch):
    monkeypatch.setenv("DATA_SOURCES", "fake")
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    assert collector.main() == 2

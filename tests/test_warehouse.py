"""Every read and write in BigQuery is scoped to one user."""

import re
from datetime import date

import pytest

from secondwind.adapters.warehouse.bigquery_reader import BigQueryReader
from secondwind.adapters.warehouse.bigquery_writer import KEYS, BigQueryWriter, merge_sql
from secondwind.adapters.warehouse.ingest_state import IngestState


class Job:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def result(self):
        return self.rows


class FakeClient:
    def __init__(self):
        self.queries = []

    def query(self, sql, job_config=None):
        params = {p.name: p.value for p in (job_config.query_parameters if job_config else [])}
        self.queries.append((sql, params))
        return Job()


READ_CALLS = [
    ("get_last_activity", ()), ("get_week_activities", ()), ("get_health_last_days", ()),
    ("get_load_last_days", ()), ("get_records", (date(2026, 1, 1),)),
    ("get_period", (date(2026, 11, 1), date(2026, 11, 20))), ("get_weekly_run_km", ()),
    ("get_load_series", ()), ("get_health_series", ()), ("get_run_efficiency", ()),
    ("get_athlete_inputs", ()), ("get_source_status", ()),
]


@pytest.mark.parametrize("method,args", READ_CALLS)
def test_every_bot_query_reads_only_this_users_rows(method, args):
    client = FakeClient()
    getattr(BigQueryReader("p", "d", "u-1", client=client), method)(*args)
    (sql, params) = client.queries[-1]
    tables = len(re.findall(r"`p\.d\.\w+`", sql))
    assert tables >= 1 and sql.count("user_id = @uid") >= tables, sql
    assert params["uid"] == "u-1"


def test_reader_and_writer_need_a_user():
    with pytest.raises(ValueError):
        BigQueryReader("p", "d", "", client=FakeClient())
    with pytest.raises(ValueError):
        BigQueryWriter("p", "d", "", client=FakeClient())
    with pytest.raises(ValueError):
        IngestState(FakeClient(), "p.d.ingest_state", "")
    with pytest.raises(ValueError):
        BigQueryReader("p", "d", "u-1", client=FakeClient())._q("SELECT 1")


def test_merge_keys_start_with_user_id():
    assert all(k[0] == "user_id" for k in KEYS.values())
    sql = merge_sql("p.d.activities", "p.d._s", ["user_id", "source", "activity_id", "name"], KEYS["activities"])
    assert "ON T.user_id = S.user_id AND T.source = S.source AND T.activity_id = S.activity_id" in sql


class WriterClient(FakeClient):
    def __init__(self, cols):
        super().__init__()
        self.cols, self.loaded = cols, []

    def get_table(self, name):
        from google.cloud import bigquery
        return type("T", (), {"schema": [bigquery.SchemaField(c, "STRING") for c in self.cols]})()

    def load_table_from_json(self, rows, stage, job_config=None):
        self.loaded.extend(rows)
        return Job()

    def delete_table(self, *a, **k):
        pass


def test_writer_stamps_its_user_and_refuses_other_users_rows():
    client = WriterClient(["user_id", "source", "date", "steps", "ingested_at"])
    w = BigQueryWriter("p", "d", "u-1", client=client)
    w.upsert("health_daily", [{"source": "garmin", "date": "2026-11-20", "steps": 1}])
    assert client.loaded[0]["user_id"] == "u-1"
    with pytest.raises(ValueError):
        w.upsert("health_daily", [{"user_id": "u-2", "source": "garmin", "date": "2026-11-20"}])
    w.stored_file_ids("garmin", "2026-11-01")
    sql, params = client.queries[-1]
    assert "user_id = @uid" in sql and params["uid"] == "u-1"


def test_ingest_state_is_per_user():
    client = FakeClient()
    st = IngestState(client, "p.d.ingest_state", "u-1")
    st.cursor("garmin")
    st.record("garmin", date(2026, 11, 20), "ok")
    for sql, params in client.queries:
        assert params["uid"] == "u-1"
    assert "user_id = @uid" in client.queries[0][0]
    assert "T.user_id = S.user_id" in client.queries[1][0] and "(user_id, source," in client.queries[1][0]

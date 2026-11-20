"""The webhook accepts only requests that carry Telegram's secret token."""

import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-token-not-real")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from secondwind.apps.bot import main  # noqa: E402

HEADER = "X-Telegram-Bot-Api-Secret-Token"


@pytest.fixture
def client(monkeypatch):
    seen = []

    async def fake_process(update):
        seen.append(update.update_id)

    monkeypatch.setattr(main, "WEBHOOK_SECRET", "s3cret-value")
    monkeypatch.setattr(main.tg, "process_update", fake_process)
    c = TestClient(main.app)   # no `with`: startup (Telegram login) does not run
    c.seen = seen
    return c


def test_valid_secret_is_processed(client):
    r = client.post("/webhook", json={"update_id": 7}, headers={HEADER: "s3cret-value"})
    assert r.status_code == 200 and client.seen == [7]


@pytest.mark.parametrize("headers", [{}, {HEADER: ""}, {HEADER: "wrong"}, {HEADER: "s3cret-value "}])
def test_missing_or_wrong_secret_is_rejected(client, headers):
    r = client.post("/webhook", json={"update_id": 8}, headers=headers)
    assert r.status_code == 403 and client.seen == []


def test_no_configured_secret_rejects_everything(client, monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_SECRET", "")
    r = client.post("/webhook", json={"update_id": 9}, headers={HEADER: ""})
    assert r.status_code == 403 and client.seen == []


def test_health_needs_no_secret(client):
    assert client.get("/health").status_code == 200


def test_startup_logs_in_and_registers_the_menu(monkeypatch):
    called = []

    async def fake_init():
        called.append("init")

    async def fake_menu(bot):
        called.append("menu")

    monkeypatch.setattr(main.tg, "initialize", fake_init)
    monkeypatch.setattr(main, "setup_menu", fake_menu)
    with TestClient(main.app) as c:
        assert c.get("/health").status_code == 200
    assert called == ["init", "menu"]

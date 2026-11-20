"""Model choice and the request sent to the model must stay as they were before the move to tiers."""

import types

import pytest

from secondwind.adapters.llm.claude import TIER_MODELS, ClaudeLLM
from secondwind.core import assistant as a
from secondwind.core import models as m

H, S, O = "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"

# (task, mode) -> model, without and with MODEL_<TASK>=opus pinned at deploy time.
EXPECTED = {
    ("chat", "economy"): (H, H), ("chat", "normal"): (S, O), ("chat", "max"): (O, O),
    ("analysis", "economy"): (H, H), ("analysis", "normal"): (S, O), ("analysis", "max"): (O, O),
    **{(t, mode): (H, O) for t in ("weekly", "alerts", "morning") for mode in m.MODES},
}
EFFORT = {"chat": "low", "analysis": "medium", "weekly": "low", "alerts": "low", "morning": "low"}


def _model(task, mode):
    return m.model_override(task, mode) or TIER_MODELS[m.tier_for(task, mode)]


@pytest.mark.parametrize("task,mode", sorted(EXPECTED))
def test_model_choice(monkeypatch, task, mode):
    for t in m.TASKS:
        monkeypatch.delenv(f"MODEL_{t.upper()}", raising=False)
    assert _model(task, mode) == EXPECTED[task, mode][0]
    assert m.effort_for(task) == EFFORT[task]
    monkeypatch.setenv(f"MODEL_{task.upper()}", O)
    assert _model(task, mode) == EXPECTED[task, mode][1]


class FakeClient:
    def __init__(self, stop_reason="end_turn", model=S):
        self.calls = []
        resp = types.SimpleNamespace(stop_reason=stop_reason, model=model, stop_details=None,
                                     usage=types.SimpleNamespace(input_tokens=1000, output_tokens=100),
                                     content=[types.SimpleNamespace(type="text", text=" ok ")])
        create = lambda **kw: self.calls.append(kw) or resp  # noqa: E731
        self.messages = types.SimpleNamespace(create=create)
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def test_request_body_and_answer(monkeypatch):
    monkeypatch.delenv("MODEL_CHAT", raising=False)
    client = FakeClient()
    ans = a.analyze(ClaudeLLM(client=client), a.ASK_PROMPT, "DATA", "chat", "ru", question="как я?")
    (body,) = client.calls
    assert body["model"] == S and body["max_tokens"] == 16000 and body["output_config"] == {"effort": "low"}
    assert body["fallbacks"] == "default" and body["betas"] == ["server-side-fallback-2026-07-01"]
    (msg,) = body["messages"]
    assert msg["role"] == "user" and "Question: как я?" in msg["content"] and "DATA" in msg["content"]
    assert "in Russian" in msg["content"] and a.GOAL_NOTE in msg["content"] and "system" not in body
    assert ans.text == "ok" and ans.input_tokens == 1000 and ans.cost_usd == pytest.approx(0.003)


def test_haiku_has_no_fallback_and_refusal_raises():
    client = FakeClient(stop_reason="refusal", model=H)
    with pytest.raises(a.Refused):
        a.analyze(ClaudeLLM(client=client), a.COACH_PROMPT, "DATA", "analysis", "en", mode="economy")
    assert client.calls[0]["model"] == H and "fallbacks" not in client.calls[0]


def test_prompt_files_use_only_known_fields():
    import string
    allowed = {"persona", "goal", "data", "format", "language", "question"}
    for name in ("coach", "health", "nutrition", "ask"):
        fields = {f for _, f, _, _ in string.Formatter().parse(a._prompt(name)) if f}
        assert fields <= allowed and {"persona", "data", "format", "language"} <= fields, name
    assert "Vita" in a.PERSONA and not a.PERSONA.endswith("\n")

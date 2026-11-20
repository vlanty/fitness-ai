"""`LLM` adapter for the Anthropic API (Claude)."""

from __future__ import annotations

import os

import anthropic

from secondwind.core.ports import LLMRequest, LLMResponse

# Tier -> Claude model.
TIER_MODELS = {
    "small": "claude-haiku-5-5",
    "medium": "claude-sonnet-5-5",
    "large": "claude-opus-5-5",
}

# USD per 1M tokens (input, output), Anthropic API list prices.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_PREFIXES = ("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5")


def supports_fallback(model: str) -> bool:
    return model.startswith(_FALLBACK_PREFIXES)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """List-price cost of one call; 0 for a model missing from PRICES (tokens are still counted)."""
    for name, (p_in, p_out) in PRICES.items():
        if model.startswith(name):
            return (input_tokens * p_in + output_tokens * p_out) / 1_000_000
    return 0.0


class ClaudeLLM:
    def __init__(self, api_key: str | None = None, client: anthropic.Anthropic | None = None) -> None:
        self._api_key = api_key
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._api_key or os.environ["ANTHROPIC_API_KEY"])
        return self._client

    def complete(self, req: LLMRequest) -> LLMResponse:
        model = req.model or TIER_MODELS[req.tier]
        params: dict = dict(model=model, max_tokens=req.max_tokens, messages=req.messages)
        if req.effort:
            params["output_config"] = {"effort": req.effort}
        if req.system:
            params["system"] = [{"type": "text", "text": b.text,
                                 **({"cache_control": {"type": "ephemeral"}} if b.cache else {})}
                                for b in req.system]
        if supports_fallback(model):
            response = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
        else:
            response = self.client.messages.create(**params)

        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        u = response.usage
        return LLMResponse(text=text, model=response.model, input_tokens=u.input_tokens,
                           output_tokens=u.output_tokens,
                           cost_usd=cost_usd(response.model, u.input_tokens, u.output_tokens),
                           stop_reason=response.stop_reason, refused=response.stop_reason == "refusal",
                           refusal_details=getattr(response, "stop_details", None))

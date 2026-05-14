"""LLM clients for cross-model brand-mention checks.

Cost guard: each provider call is metered against `MAX_COST_PER_CALL`. The
ceiling is shared across all providers in a single tool call.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Public list prices, USD per 1M tokens (input/output). Update as providers shift.
PRICES = {
    "perplexity:sonar": (1.0, 1.0),
    "perplexity:sonar-pro": (3.0, 15.0),
    "openrouter:anthropic/claude-3.5-sonnet": (3.0, 15.0),
    "openrouter:openai/gpt-4o-mini": (0.15, 0.60),
    "openrouter:google/gemini-2.0-flash-001": (0.10, 0.40),
}


@dataclass
class LLMAnswer:
    provider: str
    model: str
    text: str
    citations: list[str]
    mentioned: bool
    matched_alias: str | None
    est_cost_usd: float
    tokens_in: int
    tokens_out: int
    error: str | None = None


def _mention_check(text: str, brand: str, aliases: list[str] | None) -> tuple[bool, str | None]:
    candidates = [brand] + (aliases or [])
    for a in candidates:
        if not a:
            continue
        if re.search(rf"\b{re.escape(a)}\b", text, re.IGNORECASE):
            return True, a
    return False, None


def _est_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES.get(model_key)
    if not p:
        return 0.0
    cost = (tokens_in / 1_000_000) * p[0] + (tokens_out / 1_000_000) * p[1]
    return round(cost, 6)


async def _post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float = 30.0
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def query_perplexity(
    query: str, brand: str, aliases: list[str] | None, model: str = "sonar"
) -> LLMAnswer:
    key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    model_key = f"perplexity:{model}"
    if not key:
        return LLMAnswer("perplexity", model, "", [], False, None, 0.0, 0, 0,
                         error="PERPLEXITY_API_KEY missing")
    try:
        data = await _post_json(
            PERPLEXITY_URL,
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {"model": model, "messages": [{"role": "user", "content": query}]},
        )
    except httpx.HTTPError as exc:
        return LLMAnswer("perplexity", model, "", [], False, None, 0.0, 0, 0,
                         error=f"{exc.__class__.__name__}: {exc}")

    text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
    citations = data.get("citations") or data.get("search_results") or []
    if isinstance(citations, list):
        citations = [c if isinstance(c, str) else c.get("url", "") for c in citations]
    usage = data.get("usage", {}) or {}
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))
    mentioned, alias = _mention_check(text, brand, aliases)
    return LLMAnswer(
        provider="perplexity", model=model, text=text, citations=citations,
        mentioned=mentioned, matched_alias=alias,
        est_cost_usd=_est_cost(model_key, tokens_in, tokens_out),
        tokens_in=tokens_in, tokens_out=tokens_out,
    )


async def query_openrouter(
    query: str, brand: str, aliases: list[str] | None, model: str
) -> LLMAnswer:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model_key = f"openrouter:{model}"
    if not key:
        return LLMAnswer("openrouter", model, "", [], False, None, 0.0, 0, 0,
                         error="OPENROUTER_API_KEY missing")
    try:
        data = await _post_json(
            OPENROUTER_URL,
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/sanders-ops/ai-visibility-mcp",
                "X-Title": "ai-visibility-mcp",
            },
            {"model": model, "messages": [{"role": "user", "content": query}]},
        )
    except httpx.HTTPError as exc:
        return LLMAnswer("openrouter", model, "", [], False, None, 0.0, 0, 0,
                         error=f"{exc.__class__.__name__}: {exc}")

    text = (data.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
    usage = data.get("usage", {}) or {}
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))
    mentioned, alias = _mention_check(text, brand, aliases)
    return LLMAnswer(
        provider="openrouter", model=model, text=text, citations=[],
        mentioned=mentioned, matched_alias=alias,
        est_cost_usd=_est_cost(model_key, tokens_in, tokens_out),
        tokens_in=tokens_in, tokens_out=tokens_out,
    )


def cost_ceiling() -> float:
    try:
        return float(os.getenv("MAX_COST_PER_CALL", "0.10"))
    except ValueError:
        return 0.10

"""LLM clients for cross-model brand-mention checks.

Cost guard: each provider call is metered against `MAX_COST_PER_CALL`. The
ceiling is shared across all providers in a single tool call.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
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
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float = 30.0,
    retries: int = 2,
) -> dict[str, Any]:
    delay = 1.5
    last_exc: httpx.HTTPError | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries + 1):
            try:
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code in (429, 502, 503, 504) and attempt < retries:
                    retry_after = resp.headers.get("retry-after")
                    wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
                    await asyncio.sleep(min(wait, 8.0))
                    delay *= 2
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise
    if last_exc:
        raise last_exc
    raise httpx.HTTPError("exhausted retries with no response")


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
            {
                "model": model,
                "messages": [{"role": "user", "content": query}],
                "max_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024")),
            },
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
            {
                "model": model,
                "messages": [{"role": "user", "content": query}],
                "max_tokens": int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024")),
            },
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


def daily_cap() -> float:
    try:
        return float(os.getenv("MAX_DAILY_USD", "5.00"))
    except ValueError:
        return 5.00


def _spend_path() -> Path:
    override = os.getenv("AI_VISIBILITY_SPEND_FILE")
    if override:
        return Path(override)
    base = Path(os.getenv("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "ai-visibility-mcp" / "spend.json"


def _today_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def read_daily_spend() -> float:
    p = _spend_path()
    if not p.exists():
        return 0.0
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return 0.0
    return float(data.get(_today_key(), 0.0))


def record_spend(amount_usd: float) -> float:
    if amount_usd <= 0:
        return read_daily_spend()
    p = _spend_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    today = _today_key()
    data: dict[str, float] = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text())
            if isinstance(raw, dict):
                data = {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))}
        except (OSError, json.JSONDecodeError):
            data = {}
    cutoff_ts = time.time() - 14 * 86400
    data = {
        k: v for k, v in data.items()
        if time.mktime(time.strptime(k, "%Y-%m-%d")) >= cutoff_ts
    } if data else {}
    data[today] = data.get(today, 0.0) + amount_usd
    p.write_text(json.dumps(data, indent=2, sort_keys=True))
    return data[today]


class DailyCapReached(RuntimeError):
    pass


def assert_under_daily_cap(predicted_cost: float = 0.0) -> tuple[float, float]:
    cap = daily_cap()
    spent = read_daily_spend()
    if spent + predicted_cost > cap:
        raise DailyCapReached(
            f"daily LLM spend ceiling ${cap:.2f} reached (today: ${spent:.4f}); "
            f"refusing further calls. Override with MAX_DAILY_USD env var."
        )
    return spent, cap

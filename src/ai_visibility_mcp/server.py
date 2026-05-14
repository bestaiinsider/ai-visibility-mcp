"""ai-visibility-mcp — FastMCP server.

v0.1 exposes `check_ai_bot_access(domain)`. Roadmap in `~/Documents/Vault/02 Projects/ai-visibility-mcp.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

from .bots import KNOWN_AI_BOTS
from .robots import access_for, normalize_domain, parse

load_dotenv()

USER_AGENT = "ai-visibility-mcp/0.1 (+https://github.com/sanders-ops/ai-visibility-mcp)"
HTTP_TIMEOUT = 15.0

mcp = FastMCP("ai-visibility-mcp")


async def _fetch(url: str) -> tuple[int, dict[str, str], str]:
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        resp = await client.get(url)
        return resp.status_code, dict(resp.headers), resp.text


def _cloudflare_signals(headers: dict[str, str], status: int, body: str) -> dict[str, Any]:
    h = {k.lower(): v for k, v in headers.items()}
    server = h.get("server", "").lower()
    has_cf = "cloudflare" in server or "cf-ray" in h
    mitigated = h.get("cf-mitigated", "").lower()
    challenged = (
        mitigated in ("challenge", "block")
        or status == 403
        or "just a moment" in body[:2000].lower()
        or "attention required" in body[:2000].lower()
    )
    return {
        "cloudflare_detected": has_cf,
        "cf_ray": h.get("cf-ray"),
        "cf_mitigated": mitigated or None,
        "likely_bot_challenge": bool(has_cf and challenged),
    }


async def check_ai_bot_access_impl(domain: str) -> dict[str, Any]:
    base = normalize_domain(domain)
    robots_url = f"{base}/robots.txt"

    warnings: list[str] = []
    try:
        rstatus, rheaders, rbody = await _fetch(robots_url)
    except httpx.HTTPError as exc:
        return {
            "domain": base,
            "error": f"failed to fetch robots.txt: {exc.__class__.__name__}: {exc}",
        }

    records: list = []
    if rstatus == 200:
        records = parse(rbody)
    elif rstatus == 404:
        warnings.append("no robots.txt — all bots implicitly allowed")
    else:
        warnings.append(f"robots.txt returned HTTP {rstatus} — treating as unspecified")

    try:
        root_status, root_headers, root_body = await _fetch(base + "/")
    except httpx.HTTPError as exc:
        root_status, root_headers, root_body = 0, {}, ""
        warnings.append(f"failed to fetch root: {exc.__class__.__name__}")

    cf = _cloudflare_signals(root_headers, root_status, root_body)
    if cf["likely_bot_challenge"]:
        warnings.append(
            "Cloudflare bot-challenge detected at root — AI bots without JS will be blocked "
            "even if robots.txt allows them. Check Cloudflare → Security → Bots → AI Scrapers."
        )

    bots_report: list[dict[str, Any]] = []
    for bot in KNOWN_AI_BOTS:
        verdict = access_for(records, bot.ua, "/") if records else (
            "allowed" if rstatus == 404 else "unspecified"
        )
        has_explicit_rule = any(
            bot.ua.lower() in ua.strip().lower()
            for rec in records
            for ua in rec.user_agents
            if ua.strip() != "*"
        )
        bots_report.append({
            "user_agent": bot.ua,
            "vendor": bot.vendor,
            "purpose": bot.purpose,
            "verdict": verdict,
            "rule_source": "explicit" if has_explicit_rule else (
                "wildcard" if records else "default"
            ),
        })

    allowed = sum(1 for b in bots_report if b["verdict"] == "allowed")
    disallowed = sum(1 for b in bots_report if b["verdict"] == "disallowed")

    if disallowed >= len(bots_report) * 0.75:
        warnings.append(
            f"{disallowed}/{len(bots_report)} known AI bots are disallowed — "
            "site is largely invisible to AI search."
        )

    return {
        "domain": base,
        "robots_txt": {
            "url": robots_url,
            "status": rstatus,
            "size_bytes": len(rbody) if rstatus == 200 else 0,
            "records_parsed": len(records),
        },
        "bots": bots_report,
        "summary": {
            "total": len(bots_report),
            "allowed": allowed,
            "disallowed": disallowed,
            "unspecified": len(bots_report) - allowed - disallowed,
        },
        "cloudflare": cf,
        "warnings": warnings,
    }


@mcp.tool()
async def check_ai_bot_access(domain: str) -> dict[str, Any]:
    """Check whether AI bots can read this site.

    Fetches `/robots.txt` and the root URL. Reports per-bot allow/disallow
    plus Cloudflare AI-bot-default warning signals.

    Args:
        domain: e.g. `example.com` or `https://example.com`

    Returns:
        JSON with `domain`, `robots_txt`, `bots` (list of per-bot verdicts),
        `cloudflare`, and `warnings`.
    """
    return await check_ai_bot_access_impl(domain)


def main() -> None:
    parser = argparse.ArgumentParser(prog="ai-visibility-mcp")
    parser.add_argument("--http", action="store_true", help="serve over HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()

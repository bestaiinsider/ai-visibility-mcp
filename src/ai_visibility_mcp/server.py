"""ai-visibility-mcp — FastMCP server.

v0.1 exposes `check_ai_bot_access(domain)`. Roadmap in `~/Documents/Vault/02 Projects/ai-visibility-mcp.md`.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

from .audit import detect_spa_shell, interpret_meta_robots, parse_html
from .bots import KNOWN_AI_BOTS
from .llm import (
    LLMAnswer,
    cost_ceiling,
    query_openrouter,
    query_perplexity,
)
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


async def _fetch_optional(url: str) -> tuple[int, dict[str, str], str]:
    try:
        return await _fetch(url)
    except httpx.HTTPError:
        return 0, {}, ""


async def audit_ai_visibility_impl(domain: str) -> dict[str, Any]:
    base = normalize_domain(domain)
    bot_access = await check_ai_bot_access_impl(domain)

    root_status, _, root_body = await _fetch_optional(base + "/")
    onpage = parse_html(root_body) if root_status == 200 else {
        "title": None, "description": None, "ai_meta_tags": {},
        "open_graph": {}, "jsonld_count": 0, "jsonld_errors": [], "schema_types": [],
    }
    spa = detect_spa_shell(root_body if root_status == 200 else "")

    robots_flags: dict[str, Any] = {}
    if "robots" in onpage["ai_meta_tags"]:
        robots_flags = interpret_meta_robots(onpage["ai_meta_tags"]["robots"])

    sitemap_status, _, sitemap_body = await _fetch_optional(base + "/sitemap.xml")
    sitemap_present = sitemap_status == 200 and (
        "<urlset" in sitemap_body or "<sitemapindex" in sitemap_body
    )
    sitemap_url_count = sitemap_body.count("<loc>") if sitemap_status == 200 else 0

    llms_status, _, llms_body = await _fetch_optional(base + "/llms.txt")
    llms_full_status, _, _ = await _fetch_optional(base + "/llms-full.txt")

    warnings: list[str] = list(bot_access.get("warnings", []))
    score = 100
    score_reasons: list[str] = []

    s = bot_access.get("summary", {})
    if s.get("disallowed", 0) > 0:
        penalty = min(40, s["disallowed"] * 4)
        score -= penalty
        score_reasons.append(f"-{penalty}: {s['disallowed']} AI bots disallowed in robots.txt")

    if bot_access.get("cloudflare", {}).get("likely_bot_challenge"):
        score -= 25
        score_reasons.append("-25: Cloudflare bot challenge on root")

    if robots_flags.get("noindex"):
        score -= 30
        score_reasons.append("-30: meta robots noindex on homepage")
        warnings.append("homepage has `<meta name=robots content='noindex'>` — invisible to search/AI")
    if robots_flags.get("noai") or robots_flags.get("noimageai"):
        score -= 15
        score_reasons.append("-15: meta robots noai / noimageai")
        warnings.append("homepage opts out of AI training via meta robots noai")

    if not onpage["title"]:
        score -= 5
        score_reasons.append("-5: missing <title>")
        warnings.append("homepage missing <title>")
    if not onpage["description"]:
        score -= 5
        score_reasons.append("-5: missing meta description")
        warnings.append("homepage missing <meta name=description>")

    if onpage["jsonld_count"] == 0:
        score -= 10
        score_reasons.append("-10: no JSON-LD structured data")
        warnings.append("no JSON-LD structured data — LLMs lose entity grounding")
    elif onpage["jsonld_errors"]:
        score -= 5
        score_reasons.append(f"-5: {len(onpage['jsonld_errors'])} JSON-LD parse errors")

    if not sitemap_present:
        score -= 5
        score_reasons.append("-5: no /sitemap.xml")
        warnings.append("no /sitemap.xml found at root")

    if spa.get("likely_spa_shell"):
        score -= 20
        score_reasons.append("-20: likely empty SPA shell on server-render")
        warnings.append(
            "homepage looks like an empty JS-app shell — AI bots that don't run "
            "JS (GPTBot, ClaudeBot, PerplexityBot) will see nothing. "
            "Enable SSR / SSG / prerender."
        )

    has_llms_txt = llms_status == 200
    has_llms_full = llms_full_status == 200
    if has_llms_txt:
        score_reasons.append("+0: llms.txt present (AI-friendly signal)")
    if has_llms_full:
        score_reasons.append("+0: llms-full.txt present")

    score = max(0, min(100, score))

    return {
        "domain": base,
        "score": score,
        "score_reasons": score_reasons,
        "warnings": warnings,
        "bot_access_summary": bot_access.get("summary", {}),
        "cloudflare": bot_access.get("cloudflare", {}),
        "on_page": {
            "title": onpage["title"],
            "description": onpage["description"],
            "ai_meta_tags": onpage["ai_meta_tags"],
            "meta_robots_flags": robots_flags,
            "open_graph_count": len(onpage["open_graph"]),
            "jsonld_count": onpage["jsonld_count"],
            "jsonld_errors": onpage["jsonld_errors"],
            "schema_types": onpage["schema_types"],
        },
        "spa_shell": spa,
        "sitemap": {
            "url": base + "/sitemap.xml",
            "present": bool(sitemap_present),
            "url_count": sitemap_url_count,
        },
        "llms_txt": {
            "llms_txt_present": has_llms_txt,
            "llms_full_txt_present": has_llms_full,
        },
    }


@mcp.tool()
async def audit_ai_visibility(domain: str) -> dict[str, Any]:
    """Composite AI-visibility audit for a domain.

    Combines `check_ai_bot_access` with homepage scrape: meta robots tags
    (incl. noai/noimageai), JSON-LD structured data, sitemap.xml, llms.txt.
    Produces a 0-100 score with explainable reasons.

    Args:
        domain: e.g. `example.com` or `https://example.com`
    """
    return await audit_ai_visibility_impl(domain)


async def check_llm_mention_impl(
    brand: str,
    query: str,
    aliases: list[str] | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    if not brand or not brand.strip():
        return {"error": "brand is required"}
    if not query or not query.strip():
        return {"error": "query is required"}

    ceiling = cost_ceiling()
    chosen = models or [
        "perplexity:sonar",
        "openrouter:openai/gpt-4o-mini",
        "openrouter:google/gemini-2.0-flash-001",
    ]

    answers: list[LLMAnswer] = []
    spent = 0.0
    skipped: list[str] = []

    for m in chosen:
        if spent >= ceiling:
            skipped.append(f"{m} (cost ceiling ${ceiling:.4f} hit at ${spent:.4f})")
            continue
        if m.startswith("perplexity:"):
            ans = await query_perplexity(query, brand, aliases, model=m.split(":", 1)[1])
        elif m.startswith("openrouter:"):
            ans = await query_openrouter(query, brand, aliases, model=m.split(":", 1)[1])
        else:
            skipped.append(f"{m} (unknown provider prefix)")
            continue
        answers.append(ans)
        spent += ans.est_cost_usd

    by_model = []
    citations_union: set[str] = set()
    mention_count = 0
    for a in answers:
        if a.mentioned:
            mention_count += 1
        for c in a.citations:
            if c:
                citations_union.add(c)
        by_model.append({
            "provider": a.provider,
            "model": a.model,
            "mentioned": a.mentioned,
            "matched_alias": a.matched_alias,
            "answer_excerpt": (a.text or "")[:600],
            "citations": a.citations,
            "tokens": {"in": a.tokens_in, "out": a.tokens_out},
            "est_cost_usd": a.est_cost_usd,
            "error": a.error,
        })

    total_cost = round(sum(a.est_cost_usd for a in answers), 6)
    return {
        "brand": brand,
        "aliases": aliases or [],
        "query": query,
        "summary": {
            "models_queried": len(answers),
            "models_with_mention": mention_count,
            "models_skipped": skipped,
            "share_of_voice": round(mention_count / len(answers), 3) if answers else 0.0,
            "est_total_cost_usd": total_cost,
            "cost_ceiling_usd": ceiling,
        },
        "citations": sorted(citations_union),
        "by_model": by_model,
    }


@mcp.tool()
async def check_llm_mention(
    brand: str,
    query: str,
    aliases: list[str] | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """Check whether `brand` surfaces in LLM answers to `query`.

    Fans out the same query to multiple LLMs (Perplexity sonar, OpenAI
    gpt-4o-mini, Gemini 2.0 Flash by default) and reports per-model
    mention + citations. Cost-capped via MAX_COST_PER_CALL env var.

    Args:
        brand: brand or product name to look for in answers
        query: the user-style question to ask each model
        aliases: optional alternate names that should also count as a mention
        models: optional override, e.g. ["perplexity:sonar", "openrouter:anthropic/claude-3.5-sonnet"]
    """
    return await check_llm_mention_impl(brand, query, aliases, models)


async def compare_competitors_impl(
    your_domain: str, competitor_domains: list[str]
) -> dict[str, Any]:
    if not competitor_domains:
        return {"error": "competitor_domains must contain at least one entry"}

    all_domains = [your_domain] + list(competitor_domains)
    results = await asyncio.gather(*(audit_ai_visibility_impl(d) for d in all_domains))

    rows = []
    for d, r in zip(all_domains, results):
        rows.append({
            "domain": r.get("domain", d),
            "score": r.get("score", 0),
            "bots_allowed": r.get("bot_access_summary", {}).get("allowed", 0),
            "bots_disallowed": r.get("bot_access_summary", {}).get("disallowed", 0),
            "has_jsonld": r.get("on_page", {}).get("jsonld_count", 0) > 0,
            "schema_types": r.get("on_page", {}).get("schema_types", []),
            "has_llms_txt": r.get("llms_txt", {}).get("llms_txt_present", False),
            "sitemap_urls": r.get("sitemap", {}).get("url_count", 0),
            "cloudflare_challenge": r.get("cloudflare", {}).get("likely_bot_challenge", False),
        })

    your_row = rows[0]
    competitor_rows = rows[1:]
    competitor_avg = (
        sum(r["score"] for r in competitor_rows) / len(competitor_rows)
        if competitor_rows else 0
    )
    delta = your_row["score"] - competitor_avg
    rank = 1 + sum(1 for r in competitor_rows if r["score"] > your_row["score"])

    return {
        "your_domain": your_row["domain"],
        "rank": f"{rank} of {len(rows)}",
        "your_score": your_row["score"],
        "competitor_avg_score": round(competitor_avg, 1),
        "score_delta": round(delta, 1),
        "rows": rows,
    }


@mcp.tool()
async def compare_competitors(
    your_domain: str, competitor_domains: list[str]
) -> dict[str, Any]:
    """Side-by-side AI-visibility audit: your_domain vs competitors.

    Runs `audit_ai_visibility` in parallel for all domains and returns a
    ranked comparison (score, blocked bots, JSON-LD presence, llms.txt,
    sitemap size, Cloudflare challenge state).

    Args:
        your_domain: the domain whose visibility you're evaluating
        competitor_domains: list of competitor domains (at least 1)
    """
    return await compare_competitors_impl(your_domain, competitor_domains)


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

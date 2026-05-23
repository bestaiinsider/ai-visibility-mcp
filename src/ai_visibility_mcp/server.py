"""ai-visibility-mcp — FastMCP server.

v0.1 exposes `check_ai_bot_access(domain)`. Roadmap in `~/Documents/Vault/02 Projects/ai-visibility-mcp.md`.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP

from .audit import detect_spa_shell, interpret_meta_robots, parse_html
from .bots import KNOWN_AI_BOTS
from .llm import (
    PRICES,
    DailyCapReached,
    LLMAnswer,
    assert_under_daily_cap,
    cost_ceiling,
    daily_cap,
    query_openrouter,
    query_perplexity,
    read_daily_spend,
    record_spend,
)
from .robots import access_for, normalize_domain, parse

load_dotenv()

USER_AGENT = "ai-visibility-mcp/0.2 (+https://github.com/bestaiinsider/ai-visibility-mcp)"
HTTP_TIMEOUT = 15.0

PRIVATE_NETS: tuple[ipaddress._BaseNetwork, ...] = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local incl. AWS/GCP/Azure IMDS
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


class SSRFBlocked(ValueError):
    """Raised when a target URL resolves to a private / link-local address."""


def _assert_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SSRFBlocked(f"refusing non-HTTP scheme: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise SSRFBlocked("missing host in URL")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise SSRFBlocked(f"DNS resolution failed for {host!r}: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        for net in PRIVATE_NETS:
            if ip in net:
                raise SSRFBlocked(f"refusing private/link-local address {ip} for host {host!r}")


mcp = FastMCP("ai-visibility-mcp")


async def _fetch(url: str) -> tuple[int, dict[str, str], str]:
    _assert_public_url(url)

    async def _redirect_guard(response: httpx.Response) -> None:
        loc = response.headers.get("location")
        if loc:
            target = str(response.request.url.join(loc))
            _assert_public_url(target)

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=HTTP_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"response": [_redirect_guard]},
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
    except SSRFBlocked as exc:
        return {
            "domain": base,
            "error": f"refused: {exc}",
        }
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
    except (httpx.HTTPError, SSRFBlocked) as exc:
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
        verdict = (
            access_for(records, bot.ua, "/")
            if records
            else ("allowed" if rstatus == 404 else "unspecified")
        )
        has_explicit_rule = any(
            bot.ua.lower() in ua.strip().lower()
            for rec in records
            for ua in rec.user_agents
            if ua.strip() != "*"
        )
        bots_report.append(
            {
                "user_agent": bot.ua,
                "vendor": bot.vendor,
                "purpose": bot.purpose,
                "verdict": verdict,
                "rule_source": "explicit"
                if has_explicit_rule
                else ("wildcard" if records else "default"),
            }
        )

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


async def _fetch_optional(url: str) -> tuple[int, dict[str, str], str, str | None]:
    """Like _fetch but never raises. Returns (status, headers, body, error_kind).

    error_kind is None on success or a short class name on failure
    (e.g. 'ConnectError', 'ReadTimeout') so callers can distinguish
    "site says no" from "we couldn't reach the site at all".
    """
    try:
        s, h, b = await _fetch(url)
        return s, h, b, None
    except SSRFBlocked as exc:
        return 0, {}, "", f"SSRFBlocked: {exc}"
    except httpx.HTTPError as exc:
        return 0, {}, "", exc.__class__.__name__


async def audit_ai_visibility_impl(domain: str) -> dict[str, Any]:
    base = normalize_domain(domain)
    bot_access = await check_ai_bot_access_impl(domain)
    if "error" in bot_access and "robots_txt" not in bot_access:
        return {
            "domain": base,
            "score": 0,
            "error": bot_access["error"],
            "warnings": [
                "robots.txt unreachable — cannot audit. Verify the domain is correct and the site is up."
            ],
        }

    root_status, _, root_body, root_err = await _fetch_optional(base + "/")
    onpage = (
        parse_html(root_body)
        if root_status == 200
        else {
            "title": None,
            "description": None,
            "ai_meta_tags": {},
            "open_graph": {},
            "jsonld_count": 0,
            "jsonld_errors": [],
            "schema_types": [],
        }
    )
    spa = detect_spa_shell(root_body if root_status == 200 else "")

    robots_flags: dict[str, Any] = {}
    if "robots" in onpage["ai_meta_tags"]:
        robots_flags = interpret_meta_robots(onpage["ai_meta_tags"]["robots"])

    sitemap_status, _, sitemap_body, sitemap_err = await _fetch_optional(base + "/sitemap.xml")
    sitemap_present = sitemap_status == 200 and (
        "<urlset" in sitemap_body or "<sitemapindex" in sitemap_body
    )
    sitemap_url_count = sitemap_body.count("<loc>") if sitemap_status == 200 else 0

    llms_status, _, llms_body, llms_err = await _fetch_optional(base + "/llms.txt")
    llms_full_status, _, _, llms_full_err = await _fetch_optional(base + "/llms-full.txt")

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
        warnings.append(
            "homepage has `<meta name=robots content='noindex'>` — invisible to search/AI"
        )
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

    network_failures = {
        "root": root_err,
        "sitemap": sitemap_err,
        "llms_txt": llms_err,
        "llms_full_txt": llms_full_err,
    }
    failure_count = sum(1 for v in network_failures.values() if v)
    if failure_count >= 3:
        warnings.append(
            f"network unreachable on {failure_count}/4 audit URLs — score may be misleading"
        )

    return {
        "domain": base,
        "score": score,
        "score_reasons": score_reasons,
        "warnings": warnings,
        "network_failures": network_failures,
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

    try:
        daily_before, cap = assert_under_daily_cap(0.0)
    except DailyCapReached as exc:
        return {
            "error": str(exc),
            "daily_spend_usd": read_daily_spend(),
            "daily_cap_usd": daily_cap(),
        }

    answers: list[LLMAnswer] = []
    spent = 0.0
    skipped: list[str] = []
    max_out = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024"))

    for m in chosen:
        predicted_max = 0.0
        price = PRICES.get(m)
        if price:
            predicted_max = (max_out / 1_000_000) * price[1]

        if spent + predicted_max > ceiling:
            skipped.append(
                f"{m} (per-call ceiling ${ceiling:.4f} would be exceeded; "
                f"spent ${spent:.4f}, next call max ${predicted_max:.4f})"
            )
            continue
        try:
            assert_under_daily_cap(predicted_max)
        except DailyCapReached as exc:
            skipped.append(f"{m} (daily cap hit: {exc})")
            break

        if m.startswith("perplexity:"):
            ans = await query_perplexity(query, brand, aliases, model=m.split(":", 1)[1])
        elif m.startswith("openrouter:"):
            ans = await query_openrouter(query, brand, aliases, model=m.split(":", 1)[1])
        else:
            skipped.append(f"{m} (unknown provider prefix)")
            continue
        answers.append(ans)
        spent += ans.est_cost_usd
        record_spend(ans.est_cost_usd)

    by_model = []
    citations_union: set[str] = set()
    mention_count = 0
    for a in answers:
        if a.mentioned:
            mention_count += 1
        for c in a.citations:
            if c:
                citations_union.add(c)
        by_model.append(
            {
                "provider": a.provider,
                "model": a.model,
                "mentioned": a.mentioned,
                "matched_alias": a.matched_alias,
                "answer_excerpt": (a.text or "")[:600],
                "citations": a.citations,
                "tokens": {"in": a.tokens_in, "out": a.tokens_out},
                "est_cost_usd": a.est_cost_usd,
                "error": a.error,
            }
        )

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
            "daily_spend_usd": round(read_daily_spend(), 6),
            "daily_cap_usd": daily_cap(),
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


MAX_PARALLEL_COMPETITORS = 10


async def compare_competitors_impl(
    your_domain: str, competitor_domains: list[str]
) -> dict[str, Any]:
    if not competitor_domains:
        return {"error": "competitor_domains must contain at least one entry"}

    all_domains = [your_domain] + list(competitor_domains)
    sem = asyncio.Semaphore(MAX_PARALLEL_COMPETITORS)

    async def bounded(d: str) -> dict[str, Any]:
        async with sem:
            return await audit_ai_visibility_impl(d)

    results = await asyncio.gather(*(bounded(d) for d in all_domains))

    rows = []
    for d, r in zip(all_domains, results):
        rows.append(
            {
                "domain": r.get("domain", d),
                "score": r.get("score", 0),
                "bots_allowed": r.get("bot_access_summary", {}).get("allowed", 0),
                "bots_disallowed": r.get("bot_access_summary", {}).get("disallowed", 0),
                "has_jsonld": r.get("on_page", {}).get("jsonld_count", 0) > 0,
                "schema_types": r.get("on_page", {}).get("schema_types", []),
                "has_llms_txt": r.get("llms_txt", {}).get("llms_txt_present", False),
                "sitemap_urls": r.get("sitemap", {}).get("url_count", 0),
                "cloudflare_challenge": r.get("cloudflare", {}).get("likely_bot_challenge", False),
            }
        )

    your_row = rows[0]
    competitor_rows = rows[1:]
    competitor_avg = (
        sum(r["score"] for r in competitor_rows) / len(competitor_rows) if competitor_rows else 0
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
async def compare_competitors(your_domain: str, competitor_domains: list[str]) -> dict[str, Any]:
    """Side-by-side AI-visibility audit: your_domain vs competitors.

    Runs `audit_ai_visibility` in parallel for all domains and returns a
    ranked comparison (score, blocked bots, JSON-LD presence, llms.txt,
    sitemap size, Cloudflare challenge state).

    Args:
        your_domain: the domain whose visibility you're evaluating
        competitor_domains: list of competitor domains (at least 1)
    """
    return await compare_competitors_impl(your_domain, competitor_domains)


@mcp.tool()
async def generate_json_ld(
    url: str,
    page_type: str | None = None,
    include_breadcrumbs: bool = True,
) -> dict[str, Any]:
    """Generate Schema.org JSON-LD structured data for a page.

    Fetches the page, extracts title/description/OG tags/visible text, detects
    Schema.org type (Product, Article, Organization, FAQPage, SoftwareApplication,
    WebSite), then calls gpt-4o-mini to produce a ready-to-paste ``<script>`` block.
    Cost-capped via MAX_COST_PER_CALL and MAX_DAILY_USD.

    Args:
        url: full URL of the page, e.g. ``https://example.com/product/widget``
        page_type: Schema.org type override. Auto-detected if None.
        include_breadcrumbs: if True, include BreadcrumbList when breadcrumbs found.
    """
    from .generators.generate_json_ld import (
        generate_json_ld as _generate_json_ld,
    )

    return await _generate_json_ld(
        url,
        page_type=page_type,
        include_breadcrumbs=include_breadcrumbs,
    )


@mcp.tool()
async def generate_llms_txt(
    domain: str,
    crawl_depth: int = 1,
    max_pages: int = 30,
) -> dict[str, Any]:
    """Generate a spec-compliant llms.txt for a domain.

    Crawls the homepage + sitemap.xml (or homepage links when sitemap absent),
    extracts page titles/descriptions/snippets, then calls gpt-4o-mini once to
    produce llms.txt formatted per https://llmstxt.org/.
    Cost-capped via MAX_COST_PER_CALL and MAX_DAILY_USD.

    Args:
        domain: e.g. ``example.com`` or ``https://example.com``
        crawl_depth: 0 = homepage only; 1 = homepage + sitemap/links (default).
        max_pages: max pages to crawl (cost ceiling).
    """
    from .generators.generate_llms_txt import (
        generate_llms_txt as _generate_llms_txt,
    )

    return await _generate_llms_txt(
        domain,
        crawl_depth=crawl_depth,
        max_pages=max_pages,
    )


@mcp.tool()
async def generate_robots_patch(
    domain: str,
    allow_bots: list[str] | None = None,
    deny_bots: list[str] | None = None,
    preserve_existing: bool = True,
) -> dict[str, Any]:
    """Generate a corrected robots.txt that opens access to AI bots.

    Fetches existing robots.txt, strips existing AI-bot rules, re-generates
    them from the canonical 22-bot registry. Pure rule generation — no LLM call.

    Args:
        domain: e.g. ``example.com`` or ``https://example.com``
        allow_bots: UA tokens to allow. ``None`` = all 22 known AI bots.
        deny_bots: UA tokens to explicitly disallow. Takes priority over allow_bots.
        preserve_existing: if True, preserve non-AI-bot rules from existing robots.txt.
    """
    from .generators.generate_robots_patch import (
        generate_robots_patch as _generate_robots_patch,
    )

    return await _generate_robots_patch(
        domain,
        allow_bots=allow_bots,
        deny_bots=deny_bots,
        preserve_existing=preserve_existing,
    )


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

"""generate_llms_txt — llms.txt generator for AI agent discoverability.

Crawls the domain homepage + sitemap, extracts page summaries, then calls
gpt-4o-mini once via OpenRouter to produce a spec-compliant llms.txt file.
Spec: https://llmstxt.org/

Cost: one LLM call + N HTTP fetches. Capped by MAX_DAILY_USD + MAX_COST_PER_CALL.
"""

from __future__ import annotations

import asyncio
import os
import re
from html import unescape
from typing import Any

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_DESC_RE = re.compile(
    r"<meta\b[^>]*?\bname\s*=\s*[\"']description[\"'][^>]*?\bcontent\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
_H1_RE = re.compile(r"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_VISIBLE_RE = re.compile(
    r"<(?:p|h[1-6]|li|td|article|section)\b[^>]*>(.*?)</(?:p|h[1-6]|li|td|article|section)>",
    re.IGNORECASE | re.DOTALL,
)
_LOC_RE = re.compile(r"<loc>\s*(https?://[^<]+?)\s*</loc>", re.IGNORECASE)
_PRIORITY_RE = re.compile(r"<priority>\s*([0-9.]+)\s*</priority>", re.IGNORECASE)
_ANCHOR_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*[\"']([^\"'#?][^\"']*)[\"']", re.IGNORECASE)

_MODEL = "openai/gpt-4o-mini"
_MODEL_KEY = "openrouter:openai/gpt-4o-mini"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_CRAWL_SEMAPHORE = 5  # max parallel page fetches


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub(" ", s).strip()


def _extract_summary(html: str) -> dict[str, str | None]:
    title_m = _TITLE_RE.search(html)
    title = unescape(_strip_tags(title_m.group(1))) if title_m else None

    desc_m = _DESC_RE.search(html)
    description = unescape(desc_m.group(1)) if desc_m else None

    h1_m = _H1_RE.search(html)
    h1 = unescape(_strip_tags(h1_m.group(1))) if h1_m else None

    visible_chunks = _VISIBLE_RE.findall(html)
    visible_text = " ".join(_strip_tags(c) for c in visible_chunks)
    visible_text = re.sub(r"\s+", " ", visible_text).strip()[:200]

    return {
        "title": title or h1,
        "description": description,
        "snippet": visible_text or None,
    }


def _parse_sitemap_urls(xml: str) -> list[tuple[str, float]]:
    """Return (url, priority) pairs from sitemap XML, sorted descending by priority."""
    locs = _LOC_RE.findall(xml)
    priorities = _PRIORITY_RE.findall(xml)

    pairs: list[tuple[str, float]] = []
    for i, loc in enumerate(locs):
        try:
            pri = float(priorities[i]) if i < len(priorities) else 0.5
        except ValueError:
            pri = 0.5
        pairs.append((loc.strip(), pri))

    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs


def _extract_internal_links(html: str, base_url: str) -> list[str]:
    """Return internal absolute URLs found in <a href=> tags."""
    from urllib.parse import urljoin, urlsplit

    base_parts = urlsplit(base_url)
    base_origin = f"{base_parts.scheme}://{base_parts.netloc}"
    seen: set[str] = set()
    links: list[str] = []

    for m in _ANCHOR_RE.finditer(html):
        href = m.group(1).strip()
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.netloc != base_parts.netloc:
            continue
        canonical = f"{base_origin}{parts.path}".rstrip("/") or base_origin
        if canonical not in seen and canonical != base_url.rstrip("/"):
            seen.add(canonical)
            links.append(canonical)

    return links


def _build_llm_prompt(domain: str, pages: list[dict[str, Any]]) -> str:
    pages_block = "\n\n".join(
        f"URL: {p['url']}\n"
        f"Title: {p.get('title') or '(none)'}\n"
        f"Description: {p.get('description') or '(none)'}\n"
        f"Snippet: {p.get('snippet') or '(none)'}"
        for p in pages
    )
    return (
        f"Generate a complete llms.txt file for {domain}.\n\n"
        f"llms.txt spec (https://llmstxt.org/):\n"
        f"- First line: `# Site Name`\n"
        f"- Second line: blank\n"
        f"- Third line: `> One-sentence summary of what the site does`\n"
        f"- Then blank line and optional sections with markdown link lists\n"
        f"- Format: `- [Page Title](URL): Brief one-line description`\n\n"
        f"Pages crawled:\n{pages_block}\n\n"
        f"Instructions:\n"
        f"- Output ONLY the llms.txt content (no markdown fences, no explanation)\n"
        f"- First line must be `# {domain}` or the site's real name\n"
        f"- Include a `> ` summary line\n"
        f"- Group pages into logical sections if possible\n"
        f"- Use the actual page titles and descriptions from the crawled data\n"
        f"- Do not invent content not present in the crawled data"
    )


async def generate_llms_txt(
    domain: str,
    *,
    crawl_depth: int = 1,
    max_pages: int = 30,
) -> dict[str, Any]:
    """Generate a spec-compliant llms.txt for a domain.

    Crawls homepage + sitemap.xml (or homepage links when sitemap absent),
    extracts page summaries, calls gpt-4o-mini once to produce llms.txt
    formatted per https://llmstxt.org/.

    Args:
        domain: e.g. ``example.com`` or ``https://example.com``
        crawl_depth: 0 = homepage only; 1 = homepage + sitemap/links (default).
        max_pages: max pages to crawl (ceiling for cost control).

    Returns:
        dict with domain, content, byte_count, pages_crawled, model, cost_usd,
        warnings, paste_target. On error returns dict with ``error`` key.
    """
    import httpx

    from ..llm import (
        PRICES,
        DailyCapReached,
        _est_cost,
        _post_json,
        assert_under_daily_cap,
        cost_ceiling,
        daily_cap,
        read_daily_spend,
        record_spend,
    )
    from ..robots import normalize_domain
    from ..server import SSRFBlocked, _fetch

    base = normalize_domain(domain)
    warnings: list[str] = []

    # Spend gate
    max_out = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2048"))
    price_entry = PRICES.get(_MODEL_KEY)
    predicted_max = ((max_out / 1_000_000) * price_entry[1]) if price_entry else 0.01

    try:
        assert_under_daily_cap(predicted_max)
    except DailyCapReached as exc:
        return {
            "domain": base,
            "error": str(exc),
            "daily_spend_usd": read_daily_spend(),
            "daily_cap_usd": daily_cap(),
        }

    ceiling = cost_ceiling()
    if predicted_max > ceiling:
        return {
            "domain": base,
            "error": (
                f"predicted cost ${predicted_max:.4f} exceeds per-call ceiling ${ceiling:.4f}"
            ),
        }

    # Fetch homepage
    try:
        home_status, _home_headers, home_body = await _fetch(base + "/")
    except SSRFBlocked as exc:
        return {"domain": base, "error": f"refused: {exc}"}
    except httpx.HTTPError as exc:
        return {"domain": base, "error": f"fetch failed: {exc.__class__.__name__}: {exc}"}

    if home_status != 200:
        return {"domain": base, "error": f"HTTP {home_status} fetching homepage"}

    home_summary = _extract_summary(home_body)
    pages: list[dict[str, Any]] = [{"url": base + "/", **home_summary}]

    if crawl_depth >= 1:
        # Try sitemap first, fall back to homepage links
        candidate_urls: list[str] = []
        try:
            sitemap_status, _, sitemap_body = await _fetch(base + "/sitemap.xml")
            if sitemap_status == 200 and "<loc>" in sitemap_body:
                sitemap_pairs = _parse_sitemap_urls(sitemap_body)
                candidate_urls = [u for u, _ in sitemap_pairs if u != base + "/"]
                if not candidate_urls:
                    warnings.append("sitemap.xml found but contained no <loc> entries")
            else:
                warnings.append("no /sitemap.xml — falling back to homepage link extraction")
                candidate_urls = _extract_internal_links(home_body, base + "/")
        except (SSRFBlocked, httpx.HTTPError):
            warnings.append("could not fetch /sitemap.xml — falling back to homepage links")
            candidate_urls = _extract_internal_links(home_body, base + "/")

        candidate_urls = candidate_urls[: max_pages - 1]  # -1 for homepage already fetched

        sem = asyncio.Semaphore(_CRAWL_SEMAPHORE)

        async def _fetch_page(url: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    status, _, body = await _fetch(url)
                    if status != 200:
                        return None
                    summary = _extract_summary(body)
                    return {"url": url, **summary}
                except (SSRFBlocked, httpx.HTTPError):
                    return None

        results = await asyncio.gather(*(_fetch_page(u) for u in candidate_urls))
        pages.extend(r for r in results if r is not None)

    pages_crawled = len(pages)

    # LLM call
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {"domain": base, "error": "OPENROUTER_API_KEY missing"}

    prompt = _build_llm_prompt(base, pages)

    try:
        resp = await _post_json(
            _OPENROUTER_URL,
            {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/bestaiinsider/ai-visibility-mcp",
                "X-Title": "ai-visibility-mcp",
            },
            {
                "model": _MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a technical writer specializing in llms.txt files. "
                            "Output only the llms.txt content. No fences, no explanation."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_out,
                "temperature": 0.2,
            },
        )
    except httpx.HTTPError as exc:
        return {"domain": base, "error": f"LLM call failed: {exc.__class__.__name__}: {exc}"}

    content = (resp.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
    content = content.strip()

    usage = resp.get("usage", {}) or {}
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))
    cost_usd = _est_cost(_MODEL_KEY, tokens_in, tokens_out)
    record_spend(cost_usd)

    # Validate llms.txt format
    if not content.startswith("# "):
        warnings.append("LLM output does not start with '# ' — may not be valid llms.txt")
    if "\n> " not in content and not content.startswith("> "):
        warnings.append("LLM output missing '> ' summary line")

    if not content.endswith("\n"):
        content += "\n"

    return {
        "domain": base,
        "content": content,
        "byte_count": len(content.encode()),
        "pages_crawled": pages_crawled,
        "model": _MODEL,
        "cost_usd": cost_usd,
        "warnings": warnings,
        "paste_target": "/llms.txt",
    }

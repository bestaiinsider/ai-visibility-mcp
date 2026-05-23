"""generate_json_ld — Schema.org JSON-LD generator for AI visibility.

Fetches the target URL (SSRF-guarded), extracts page data, then calls
gpt-4o-mini via OpenRouter to produce ready-to-paste JSON-LD. One LLM call
per invocation, cost-capped via MAX_DAILY_USD + MAX_COST_PER_CALL.
"""

from __future__ import annotations

import json
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
_OG_RE = re.compile(
    r"<meta\b[^>]*?\bproperty\s*=\s*[\"'](og:[^\"']+)[\"'][^>]*?\bcontent\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
_IMG_RE = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_VISIBLE_RE = re.compile(
    r"<(?:p|h[1-6]|li|td|article|section)\b[^>]*>(.*?)</(?:p|h[1-6]|li|td|article|section)>",
    re.IGNORECASE | re.DOTALL,
)
_BREADCRUMB_RE = re.compile(
    r"<(?:nav|ol|ul)[^>]*(?:breadcrumb|crumb)[^>]*>(.*?)</(?:nav|ol|ul)>",
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_DATE_RE = re.compile(
    r"<(?:time|span|div|meta)\b[^>]*(?:datetime|pubdate|date)[^>]*>([^<]+)<",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r"<(?:span|div|a|meta)\b[^>]*(?:author|byline)[^>]*>([^<]+)<",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(r"(?:\$|€|£|USD|EUR|GBP)\s*(\d+(?:\.\d{1,2})?)", re.IGNORECASE)
_FAQ_Q_RE = re.compile(r"<(?:h[2-6]|dt|summary)[^>]*>([^<]{10,200})<", re.IGNORECASE)

# Schema.org required / recommended fields per type
_REQUIRED: dict[str, list[str]] = {
    "Organization": ["name", "url"],
    "WebSite": ["name", "url"],
    "Product": ["name", "description", "offers"],
    "Article": ["headline", "author", "datePublished"],
    "FAQPage": ["mainEntity"],
    "SoftwareApplication": ["name", "applicationCategory", "operatingSystem"],
}
_RECOMMENDED: dict[str, list[str]] = {
    "Organization": ["logo", "sameAs", "contactPoint"],
    "WebSite": ["potentialAction", "description"],
    "Product": ["image", "aggregateRating", "brand"],
    "Article": ["image", "publisher", "dateModified"],
    "FAQPage": [],
    "SoftwareApplication": ["offers", "screenshot", "aggregateRating"],
}

_MODEL = "openai/gpt-4o-mini"
_MODEL_KEY = "openrouter:openai/gpt-4o-mini"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub(" ", s).strip()


def _extract_page_data(html: str, url: str) -> dict[str, Any]:
    title_m = _TITLE_RE.search(html)
    title = unescape(_strip_tags(title_m.group(1))) if title_m else None

    desc_m = _DESC_RE.search(html)
    description = unescape(desc_m.group(1)) if desc_m else None

    h1_m = _H1_RE.search(html)
    h1 = unescape(_strip_tags(h1_m.group(1))) if h1_m else None

    og: dict[str, str] = {}
    for m in _OG_RE.finditer(html):
        og[m.group(1).lower()] = unescape(m.group(2))

    imgs = [m.group(1) for m in _IMG_RE.finditer(html)][:5]

    visible_chunks = _VISIBLE_RE.findall(html)
    visible_text = " ".join(_strip_tags(c) for c in visible_chunks)
    visible_text = re.sub(r"\s+", " ", visible_text).strip()[:1000]

    breadcrumb_m = _BREADCRUMB_RE.search(html)
    breadcrumbs: list[str] = []
    if breadcrumb_m:
        bc_html = breadcrumb_m.group(1)
        breadcrumbs = [
            unescape(_strip_tags(a)) for a in _ANCHOR_RE.findall(bc_html) if _strip_tags(a)
        ]

    date_m = _DATE_RE.search(html)
    pub_date = date_m.group(1).strip() if date_m else None

    author_m = _AUTHOR_RE.search(html)
    author = author_m.group(1).strip() if author_m else None

    prices = _PRICE_RE.findall(html)
    price = prices[0] if prices else None

    faq_questions = [m.group(1).strip() for m in _FAQ_Q_RE.finditer(html)]

    return {
        "url": url,
        "title": title,
        "description": description,
        "h1": h1,
        "open_graph": og,
        "images": imgs,
        "visible_text": visible_text,
        "breadcrumbs": breadcrumbs,
        "pub_date": pub_date,
        "author": author,
        "price": price,
        "faq_questions": faq_questions[:5],
    }


def _detect_page_type(data: dict[str, Any]) -> str:
    url = data["url"].lower()
    og_type = data["open_graph"].get("og:type", "").lower()

    if og_type in ("product", "product.item"):
        return "Product"
    if og_type == "article":
        return "Article"

    if re.search(r"/product[s/]|/item[s/]|/shop/", url):
        return "Product"
    if re.search(r"/blog/|/news/|/post[s/]|/article[s/]", url):
        return "Article"
    if len(data.get("faq_questions", [])) >= 3:
        return "FAQPage"
    if re.search(r"github\.com|/app/|/software/|/download", url):
        return "SoftwareApplication"

    # Root or near-root → Organization, otherwise WebSite
    path = url.split("?")[0].rstrip("/")
    depth = path.count("/") - 2  # subtract scheme slashes
    return "Organization" if depth <= 0 else "WebSite"


def _build_prompt(data: dict[str, Any], page_type: str, include_breadcrumbs: bool) -> str:
    breadcrumb_note = (
        "\n- Also include a BreadcrumbList entity for the breadcrumbs listed above."
        if include_breadcrumbs and data["breadcrumbs"]
        else ""
    )
    return (
        f"Generate a Schema.org JSON-LD block for a {page_type} page.\n\n"
        f"Page data extracted:\n"
        f"- URL: {data['url']}\n"
        f"- Title: {data['title'] or '(none)'}\n"
        f"- Description: {data['description'] or '(none)'}\n"
        f"- H1: {data['h1'] or '(none)'}\n"
        f"- OpenGraph: {json.dumps(data['open_graph']) if data['open_graph'] else '(none)'}\n"
        f"- Images: {data['images'] or '(none)'}\n"
        f"- Visible text (first 1000 chars): {data['visible_text'] or '(none)'}\n"
        f"- Breadcrumbs: {data['breadcrumbs'] or '(none)'}\n"
        f"- Published date: {data['pub_date'] or '(none)'}\n"
        f"- Author: {data['author'] or '(none)'}\n"
        f"- Price: {data['price'] or '(none)'}\n"
        f"- FAQ questions: {data['faq_questions'] or '(none)'}\n\n"
        f"Instructions:\n"
        f"- Output ONLY a valid JSON object (no markdown, no explanation).\n"
        f'- Use @context "https://schema.org".\n'
        f"- @type must be {page_type}.\n"
        f"- Include all required Schema.org fields for {page_type}.\n"
        f"- Fill in fields from the page data above; omit fields you cannot infer.\n"
        f"- Do not invent data not present in the page data above.{breadcrumb_note}"
    )


async def generate_json_ld(
    url: str,
    *,
    page_type: str | None = None,
    include_breadcrumbs: bool = True,
) -> dict[str, Any]:
    """Generate Schema.org JSON-LD for a page.

    Fetches the URL (SSRF-guarded), extracts page data, calls gpt-4o-mini once
    via OpenRouter to produce valid Schema.org JSON-LD, validates it, and wraps
    it in a ready-to-paste <script> tag.

    Args:
        url: full URL of the page, e.g. ``https://example.com/product/widget``
        page_type: Schema.org type override. Auto-detected if None.
        include_breadcrumbs: if True, include BreadcrumbList when breadcrumbs found.

    Returns:
        dict with url, page_type_detected, json_ld, script_tag, validation,
        model, cost_usd, paste_target. On error returns dict with ``error`` key.
    """
    import httpx

    from ..llm import (
        PRICES,
        DailyCapReached,
        _post_json,
        assert_under_daily_cap,
        cost_ceiling,
        daily_cap,
        read_daily_spend,
        record_spend,
    )
    from ..server import SSRFBlocked, _fetch

    # Spend gate
    max_out = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024"))
    price_entry = PRICES.get(_MODEL_KEY)
    predicted_max = ((max_out / 1_000_000) * price_entry[1]) if price_entry else 0.01

    try:
        assert_under_daily_cap(predicted_max)
    except DailyCapReached as exc:
        return {
            "url": url,
            "error": str(exc),
            "daily_spend_usd": read_daily_spend(),
            "daily_cap_usd": daily_cap(),
        }

    ceiling = cost_ceiling()
    if predicted_max > ceiling:
        return {
            "url": url,
            "error": (
                f"predicted cost ${predicted_max:.4f} exceeds per-call ceiling ${ceiling:.4f}"
            ),
        }

    # Fetch page
    try:
        status, _headers, body = await _fetch(url)
    except SSRFBlocked as exc:
        return {"url": url, "error": f"refused: {exc}"}
    except httpx.HTTPError as exc:
        return {"url": url, "error": f"fetch failed: {exc.__class__.__name__}: {exc}"}

    if status != 200:
        return {"url": url, "error": f"HTTP {status} fetching {url}"}

    # Extract and detect
    page_data = _extract_page_data(body, url)
    detected_type = page_type or _detect_page_type(page_data)

    # LLM call
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        return {"url": url, "error": "OPENROUTER_API_KEY missing"}

    prompt = _build_prompt(page_data, detected_type, include_breadcrumbs)

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
                            "You are a Schema.org structured data expert. "
                            "Output only valid JSON. No markdown fences, no explanation."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_out,
                "temperature": 0.1,
            },
        )
    except httpx.HTTPError as exc:
        return {"url": url, "error": f"LLM call failed: {exc.__class__.__name__}: {exc}"}

    raw_text = (resp.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""
    usage = resp.get("usage", {}) or {}
    tokens_in = int(usage.get("prompt_tokens", 0))
    tokens_out = int(usage.get("completion_tokens", 0))

    from ..llm import _est_cost

    cost_usd = _est_cost(_MODEL_KEY, tokens_in, tokens_out)
    record_spend(cost_usd)

    # Parse JSON output (strip accidental markdown fences)
    clean = raw_text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)

    try:
        json_ld = json.loads(clean)
    except json.JSONDecodeError as exc:
        return {
            "url": url,
            "page_type_detected": detected_type,
            "error": f"LLM output not valid JSON: {exc}",
            "raw_output": raw_text[:500],
            "model": _MODEL,
            "cost_usd": cost_usd,
        }

    # Validate
    required = _REQUIRED.get(detected_type, [])
    recommended = _RECOMMENDED.get(detected_type, [])
    present_keys = set(json_ld.keys()) if isinstance(json_ld, dict) else set()

    required_present = [f for f in required if f in present_keys]
    required_missing = [f for f in required if f not in present_keys]
    recommended_missing = [f for f in recommended if f not in present_keys]

    validation_warnings: list[str] = []
    if required_missing:
        validation_warnings.append(f"Missing required fields: {', '.join(required_missing)}")

    script_tag = f'<script type="application/ld+json">\n{json.dumps(json_ld, indent=2)}\n</script>'

    return {
        "url": url,
        "page_type_detected": detected_type,
        "json_ld": json_ld,
        "script_tag": script_tag,
        "validation": {
            "schema_org_valid": not required_missing,
            "required_fields_present": required_present,
            "recommended_missing": recommended_missing,
            "warnings": validation_warnings,
        },
        "model": _MODEL,
        "cost_usd": cost_usd,
        "paste_target": "inside <head> of the page",
    }

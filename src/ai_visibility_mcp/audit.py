"""On-page AI-visibility audit: meta robots, JSON-LD, sitemap, llms.txt."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any

META_RE = re.compile(
    r"<meta\b[^>]*?\bname\s*=\s*[\"']([^\"']+)[\"'][^>]*?\bcontent\s*=\s*[\"']([^\"']*)[\"'][^>]*>",
    re.IGNORECASE,
)
META_RE_REV = re.compile(
    r"<meta\b[^>]*?\bcontent\s*=\s*[\"']([^\"']*)[\"'][^>]*?\bname\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
DESC_RE = re.compile(
    r"<meta\b[^>]*?\bname\s*=\s*[\"']description[\"'][^>]*?\bcontent\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)
JSONLD_RE = re.compile(
    r"<script\b[^>]*?type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
OG_RE = re.compile(
    r"<meta\b[^>]*?\bproperty\s*=\s*[\"'](og:[^\"']+)[\"'][^>]*?\bcontent\s*=\s*[\"']([^\"']*)[\"']",
    re.IGNORECASE,
)

AI_META_NAMES = {
    "robots",
    "googlebot",
    "googlebot-news",
    "google-extended",
    "gptbot",
    "chatgpt-user",
    "oai-searchbot",
    "claudebot",
    "claude-user",
    "anthropic-ai",
    "perplexitybot",
    "applebot-extended",
    "ccbot",
    "bytespider",
}


def parse_html(html: str) -> dict[str, Any]:
    metas: dict[str, str] = {}
    for m in META_RE.finditer(html):
        metas[m.group(1).lower()] = unescape(m.group(2))
    for m in META_RE_REV.finditer(html):
        metas.setdefault(m.group(2).lower(), unescape(m.group(1)))

    og: dict[str, str] = {}
    for m in OG_RE.finditer(html):
        og[m.group(1).lower()] = unescape(m.group(2))

    title_m = TITLE_RE.search(html)
    title = unescape(title_m.group(1).strip()) if title_m else None
    desc_m = DESC_RE.search(html)
    description = unescape(desc_m.group(1)) if desc_m else None

    ai_metas: dict[str, str] = {k: v for k, v in metas.items() if k in AI_META_NAMES}

    jsonld_blocks: list[Any] = []
    jsonld_errors: list[str] = []
    for m in JSONLD_RE.finditer(html):
        raw = m.group(1).strip()
        try:
            jsonld_blocks.append(json.loads(raw))
        except json.JSONDecodeError as e:
            jsonld_errors.append(f"{e.__class__.__name__}: {e.msg}")

    schema_types: list[str] = []
    for block in jsonld_blocks:
        schema_types.extend(_extract_types(block))

    return {
        "title": title,
        "description": description,
        "ai_meta_tags": ai_metas,
        "open_graph": og,
        "jsonld_count": len(jsonld_blocks),
        "jsonld_errors": jsonld_errors,
        "schema_types": sorted(set(schema_types)),
    }


def _extract_types(node: Any) -> list[str]:
    out: list[str] = []
    if isinstance(node, dict):
        t = node.get("@type")
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, list):
            out.extend(x for x in t if isinstance(x, str))
        for v in node.values():
            out.extend(_extract_types(v))
    elif isinstance(node, list):
        for item in node:
            out.extend(_extract_types(item))
    return out


def interpret_meta_robots(value: str) -> dict[str, bool]:
    tokens = {t.strip().lower() for t in value.split(",")}
    return {
        "noindex": "noindex" in tokens,
        "nofollow": "nofollow" in tokens,
        "noai": "noai" in tokens,
        "noimageai": "noimageai" in tokens,
        "max_snippet_zero": "max-snippet:0" in {t for t in tokens},
    }

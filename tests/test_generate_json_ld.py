"""Tests for generate_json_ld.

Uses AsyncMock to stub _fetch (server.py) and _post_json (llm.py) — no real
HTTP or LLM calls. asyncio_mode = "auto" set in pyproject.toml.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from ai_visibility_mcp.generators.generate_json_ld import generate_json_ld
from ai_visibility_mcp.server import SSRFBlocked

_PRODUCT_HTML = """
<html><head>
<title>Widget Pro — Best Widget</title>
<meta name="description" content="The best widget for your needs.">
<meta property="og:type" content="product">
<meta property="og:title" content="Widget Pro">
</head><body>
<h1>Widget Pro</h1>
<p>A professional widget for demanding users. Price: $29.99</p>
<img src="https://example.com/widget.jpg">
</body></html>
"""

_ARTICLE_HTML = """
<html><head>
<title>How to Boost AI Visibility</title>
<meta name="description" content="A guide to boosting AI visibility for your site.">
<meta property="og:type" content="article">
</head><body>
<h1>How to Boost AI Visibility</h1>
<div class="author">Jane Doe</div>
<time datetime="2026-05-01">May 1, 2026</time>
<p>AI visibility is critical for modern sites.</p>
</body></html>
"""

_SPARSE_HTML = """<html><head></head><body><p>Hello</p></body></html>"""


def _llm_response(json_obj: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(json_obj)}}],
        "usage": {"prompt_tokens": 80, "completion_tokens": 60},
    }


async def test_product_page_returns_product_schema():
    """Product OG type → Product schema with name, description, offers."""
    product_ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Widget Pro",
        "description": "The best widget for your needs.",
        "offers": {"@type": "Offer", "price": "29.99"},
    }
    mock_fetch = AsyncMock(return_value=(200, {}, _PRODUCT_HTML))
    mock_post = AsyncMock(return_value=_llm_response(product_ld))

    with (
        patch("ai_visibility_mcp.server._fetch", mock_fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_json_ld("https://example.com/products/widget-pro")

    assert "error" not in result
    assert result["page_type_detected"] == "Product"
    assert result["json_ld"]["@type"] == "Product"
    assert result["json_ld"]["name"] == "Widget Pro"
    assert "offers" in result["json_ld"]
    assert result["validation"]["schema_org_valid"] is True
    assert result["paste_target"] == "inside <head> of the page"
    assert result["model"] == "openai/gpt-4o-mini"


async def test_article_page_returns_article_schema():
    """Article OG type → Article schema with headline, author, datePublished."""
    article_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": "How to Boost AI Visibility",
        "author": {"@type": "Person", "name": "Jane Doe"},
        "datePublished": "2026-05-01",
    }
    mock_fetch = AsyncMock(return_value=(200, {}, _ARTICLE_HTML))
    mock_post = AsyncMock(return_value=_llm_response(article_ld))

    with (
        patch("ai_visibility_mcp.server._fetch", mock_fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_json_ld("https://example.com/blog/ai-visibility")

    assert "error" not in result
    assert result["page_type_detected"] == "Article"
    assert result["json_ld"]["@type"] == "Article"
    assert "headline" in result["json_ld"]
    assert "author" in result["json_ld"]
    assert "datePublished" in result["json_ld"]
    assert result["validation"]["schema_org_valid"] is True


async def test_missing_required_fields_adds_warnings():
    """LLM omits required fields → warnings list them, schema_org_valid is False."""
    incomplete_ld = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Widget",
        # missing "description" and "offers"
    }
    mock_fetch = AsyncMock(return_value=(200, {}, _SPARSE_HTML))
    mock_post = AsyncMock(return_value=_llm_response(incomplete_ld))

    with (
        patch("ai_visibility_mcp.server._fetch", mock_fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_json_ld("https://example.com/products/x", page_type="Product")

    assert "error" not in result
    assert result["validation"]["schema_org_valid"] is False
    assert result["validation"]["warnings"]
    missing_text = result["validation"]["warnings"][0]
    assert "description" in missing_text or "offers" in missing_text


async def test_output_is_valid_json():
    """json_ld field can be round-tripped through json.loads / json.dumps."""
    org_ld = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "Example Corp",
        "url": "https://example.com",
    }
    mock_fetch = AsyncMock(return_value=(200, {}, _SPARSE_HTML))
    mock_post = AsyncMock(return_value=_llm_response(org_ld))

    with (
        patch("ai_visibility_mcp.server._fetch", mock_fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_json_ld("https://example.com/")

    assert "error" not in result
    # json_ld must be a dict (already parsed)
    assert isinstance(result["json_ld"], dict)
    # script_tag wraps the JSON correctly
    tag = result["script_tag"]
    assert tag.startswith('<script type="application/ld+json">')
    assert tag.endswith("</script>")
    # Extract and re-parse the JSON inside the tag
    inner = tag.split("\n", 1)[1].rsplit("\n", 1)[0]
    parsed = json.loads(inner)
    assert parsed["@type"] == "Organization"


async def test_ssrf_refusal_returns_error():
    """_fetch raising SSRFBlocked → result has 'error' key, no json_ld."""
    mock_fetch = AsyncMock(side_effect=SSRFBlocked("refusing private address"))

    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_json_ld("http://169.254.169.254/")

    assert "error" in result
    assert "refused" in result["error"]
    assert "json_ld" not in result


async def test_daily_cap_exceeded_returns_error():
    """Daily spend cap hit → error response with spend info, no LLM call."""
    from ai_visibility_mcp.llm import DailyCapReached

    mock_fetch = AsyncMock(return_value=(200, {}, _SPARSE_HTML))
    mock_cap = MagicMock(side_effect=DailyCapReached("daily cap $5.00 reached"))

    with (
        patch("ai_visibility_mcp.server._fetch", mock_fetch),
        patch("ai_visibility_mcp.llm.assert_under_daily_cap", mock_cap),
    ):
        result = await generate_json_ld("https://example.com/")

    assert "error" in result
    assert "daily" in result["error"].lower() or "cap" in result["error"].lower()
    assert "json_ld" not in result

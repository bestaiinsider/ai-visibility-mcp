"""Tests for generate_llms_txt.

Uses AsyncMock to stub _fetch and _post_json — no real HTTP or LLM calls.
asyncio_mode = "auto" set in pyproject.toml.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from ai_visibility_mcp.generators.generate_llms_txt import generate_llms_txt
from ai_visibility_mcp.server import SSRFBlocked

_SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/about</loc><priority>0.8</priority></url>
  <url><loc>https://example.com/blog</loc><priority>0.7</priority></url>
  <url><loc>https://example.com/contact</loc><priority>0.5</priority></url>
</urlset>"""

_HOME_HTML = """
<html><head>
<title>Example Corp — AI Tools</title>
<meta name="description" content="We build AI tools for businesses.">
</head><body><h1>Welcome to Example Corp</h1>
<p>We help companies improve AI visibility.</p>
</body></html>
"""

_ABOUT_HTML = """
<html><head><title>About Us</title>
<meta name="description" content="Learn about our team.">
</head><body><h1>About Example Corp</h1><p>Founded 2025.</p></body></html>
"""

_BLOG_HTML = """
<html><head><title>Blog</title>
<meta name="description" content="Latest news and articles.">
</head><body><h1>Blog</h1><p>Read our latest posts.</p></body></html>
"""

_CONTACT_HTML = """
<html><head><title>Contact</title></head><body><h1>Contact Us</h1>
<p>Reach us at hello@example.com</p></body></html>
"""

_LLMS_TXT = (
    "# Example Corp\n\n"
    "> AI tools for businesses.\n\n"
    "## Pages\n"
    "- [Home](https://example.com/): We help companies improve AI visibility.\n"
    "- [About Us](https://example.com/about): Learn about our team.\n"
    "- [Blog](https://example.com/blog): Latest news and articles.\n"
    "- [Contact](https://example.com/contact): Get in touch.\n"
)


def _llm_response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 200, "completion_tokens": 100},
    }


def _make_fetch(responses: dict[str, tuple]) -> AsyncMock:
    """Build an AsyncMock for _fetch that routes by URL substring."""

    async def _side_effect(url: str) -> tuple:
        for key, val in responses.items():
            if key in url:
                return val
        return (404, {}, "")

    return AsyncMock(side_effect=_side_effect)


async def test_three_pages_from_sitemap():
    """Sitemap with 3 URLs → 4 pages crawled (homepage + 3), all appear in LLM prompt."""
    fetch = _make_fetch(
        {
            "/sitemap.xml": (200, {}, _SITEMAP_XML),
            "example.com/": (200, {}, _HOME_HTML),
            "/about": (200, {}, _ABOUT_HTML),
            "/blog": (200, {}, _BLOG_HTML),
            "/contact": (200, {}, _CONTACT_HTML),
        }
    )
    mock_post = AsyncMock(return_value=_llm_response(_LLMS_TXT))

    with (
        patch("ai_visibility_mcp.server._fetch", fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_llms_txt("example.com")

    assert "error" not in result
    assert result["pages_crawled"] == 4
    assert result["content"].startswith("# ")
    assert "> " in result["content"]
    assert result["byte_count"] > 0
    assert result["paste_target"] == "/llms.txt"
    assert result["model"] == "openai/gpt-4o-mini"


async def test_sitemap_404_falls_back_to_homepage_links():
    """404 on sitemap.xml → warning added, crawls homepage anchor links instead."""
    home_with_links = _HOME_HTML.replace(
        "</body>",
        '<a href="/features">Features</a> <a href="/pricing">Pricing</a></body>',
    )
    features_html = (
        "<html><head><title>Features</title></head><body><p>Our features.</p></body></html>"
    )
    pricing_html = (
        "<html><head><title>Pricing</title></head><body><p>Our pricing.</p></body></html>"
    )

    fetch = _make_fetch(
        {
            "example.com/": (200, {}, home_with_links),
            "/features": (200, {}, features_html),
            "/pricing": (200, {}, pricing_html),
        }
    )
    mock_post = AsyncMock(return_value=_llm_response(_LLMS_TXT))

    with (
        patch("ai_visibility_mcp.server._fetch", fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_llms_txt("example.com")

    assert "error" not in result
    assert any("sitemap" in w.lower() for w in result["warnings"])
    assert result["pages_crawled"] >= 1


async def test_crawl_depth_zero_homepage_only():
    """crawl_depth=0 → only homepage fetched, no sitemap or link crawl."""
    fetch = _make_fetch({"example.com/": (200, {}, _HOME_HTML)})
    mock_post = AsyncMock(return_value=_llm_response(_LLMS_TXT))

    with (
        patch("ai_visibility_mcp.server._fetch", fetch),
        patch("ai_visibility_mcp.llm._post_json", mock_post),
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
    ):
        result = await generate_llms_txt("example.com", crawl_depth=0)

    assert "error" not in result
    assert result["pages_crawled"] == 1
    # sitemap should NOT have been fetched
    called_urls = [str(call.args[0]) for call in fetch.call_args_list]
    assert not any("/sitemap.xml" in u for u in called_urls)


async def test_ssrf_refusal_returns_error():
    """_fetch raising SSRFBlocked on homepage → error key, no content."""
    mock_fetch = AsyncMock(side_effect=SSRFBlocked("refusing private address"))

    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_llms_txt("169.254.169.254")

    assert "error" in result
    assert "refused" in result["error"]
    assert "content" not in result


async def test_daily_cap_exceeded_returns_error():
    """Daily spend cap hit → error response, no fetch or LLM call."""
    from ai_visibility_mcp.llm import DailyCapReached

    mock_cap = MagicMock(side_effect=DailyCapReached("daily cap $5.00 reached"))

    with patch("ai_visibility_mcp.llm.assert_under_daily_cap", mock_cap):
        result = await generate_llms_txt("example.com")

    assert "error" in result
    assert "content" not in result

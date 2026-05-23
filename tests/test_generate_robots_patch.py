"""Tests for generate_robots_patch.

Uses AsyncMock to stub _fetch from server.py — no real HTTP calls.
asyncio_mode = "auto" is set in pyproject.toml so no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from ai_visibility_mcp.bots import KNOWN_AI_BOTS
from ai_visibility_mcp.generators.generate_robots_patch import generate_robots_patch
from ai_visibility_mcp.server import SSRFBlocked

ALL_BOT_COUNT = len(KNOWN_AI_BOTS)  # 22


async def test_empty_existing_produces_all_ai_bots():
    """404 on robots.txt → patch has all 22 AI bot Allow entries."""
    mock_fetch = AsyncMock(return_value=(404, {}, ""))
    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_robots_patch("example.com")

    assert result["existing_robots"] is None
    assert len(result["bots_allowed"]) == ALL_BOT_COUNT
    assert result["bots_denied"] == []
    assert "GPTBot" in result["new_robots"]
    assert "ClaudeBot" in result["new_robots"]
    assert "Allow: /" in result["new_robots"]
    assert "paste_target" in result
    assert result["cloudflare_warning"] is None


async def test_admin_block_preserved():
    """Existing admin Disallow is preserved; AI bot Allow section appended."""
    existing = "User-agent: *\nDisallow: /admin\n"
    mock_fetch = AsyncMock(return_value=(200, {}, existing))
    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_robots_patch("example.com")

    assert result["existing_robots"] == existing
    new = result["new_robots"]
    assert "Disallow: /admin" in new
    assert "GPTBot" in new
    assert "Allow: /" in new
    # AI bots come after the existing wildcard block
    admin_pos = new.index("Disallow: /admin")
    gptbot_pos = new.index("GPTBot")
    assert admin_pos < gptbot_pos


async def test_custom_allow_bots_single_entry():
    """allow_bots=['GPTBot'] → only GPTBot in output, no other AI bots."""
    mock_fetch = AsyncMock(return_value=(404, {}, ""))
    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_robots_patch("example.com", allow_bots=["GPTBot"])

    assert result["bots_allowed"] == ["GPTBot"]
    assert "GPTBot" in result["new_robots"]
    assert "ClaudeBot" not in result["new_robots"]
    assert "PerplexityBot" not in result["new_robots"]


async def test_cloudflare_detected_adds_warning():
    """cf-ray header in robots.txt response → cloudflare_warning set."""
    cf_headers = {"cf-ray": "abc123-EWR", "server": "cloudflare"}
    existing = "User-agent: *\nAllow: /\n"
    mock_fetch = AsyncMock(return_value=(200, cf_headers, existing))
    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_robots_patch("example.com")

    assert result["cloudflare_warning"] is not None
    assert "Cloudflare" in result["cloudflare_warning"]
    assert "WAF" in result["cloudflare_warning"]


async def test_ssrf_refusal_returns_error():
    """_fetch raising SSRFBlocked → result has 'error' key, no partial data."""
    mock_fetch = AsyncMock(side_effect=SSRFBlocked("refusing private address"))
    with patch("ai_visibility_mcp.server._fetch", mock_fetch):
        result = await generate_robots_patch("169.254.169.254")

    assert "error" in result
    assert "refused" in result["error"]
    assert "new_robots" not in result
    assert "bots_allowed" not in result

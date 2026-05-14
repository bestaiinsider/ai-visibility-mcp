# ai-visibility-mcp

> MCP server that audits how AI sees your website. Robots, schema, LLM mentions, Cloudflare AI defaults — all in one tool call.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/) [![MCP](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Most websites are accidentally invisible to AI search. Cloudflare's bot-management defaults block GPTBot / ClaudeBot / PerplexityBot. SPAs render an empty `<div id="root">` to crawlers that don't run JS. Marketing teams have no idea their brand isn't surfacing in ChatGPT, Claude, or Perplexity answers — until traffic dries up.

`ai-visibility-mcp` answers four questions from any MCP client:

1. Can AI bots read this site?
2. What does the structured-data + meta-robots story look like to an LLM?
3. Does this brand surface in LLM-generated answers — and which models?
4. How does this site compare to its competitors?

## Tools

| Tool | Purpose | Needs API keys? |
|---|---|---|
| `check_ai_bot_access(domain)` | Per-bot robots.txt + Cloudflare AI-default flag for 22 AI user-agents | No |
| `audit_ai_visibility(domain)` | 0-100 composite score with explainable deductions (robots, meta, JSON-LD, sitemap, llms.txt, SPA shell) | No |
| `check_llm_mention(brand, query, aliases?, models?)` | Cross-model brand surfacing (Perplexity sonar + OpenAI gpt-4o-mini + Gemini 2.0 Flash by default) | Yes |
| `compare_competitors(your_domain, competitor_domains[])` | Parallel ranked audit, max 10 in flight | No |

## Why this exists

- **Cloudflare flipped defaults in 2024-2025** to block AI scrapers. Most site owners never updated their config, so AI bots get challenged and bounce.
- **MCP marketplaces shipped in 2026** (MCP Hive, Smithery, mcp.so, Glama). Every AI agent needs tools that can audit the real web. This is one.
- **Brand visibility in LLM answers is the new SEO.** Nobody has a clean stack for measuring it from a single MCP call.

## Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/krissanders/ai-visibility-mcp
cd ai-visibility-mcp
uv sync
cp .env.example .env  # fill in PERPLEXITY_API_KEY / OPENROUTER_API_KEY
```

## Run

```bash
# stdio transport — Claude Desktop / Claude Code
uv run ai-visibility-mcp

# HTTP transport — remote agents
uv run ai-visibility-mcp --http --port 8000
```

## Claude Desktop / Claude Code config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (Desktop) or `~/.claude.json` (CLI):

```json
{
  "mcpServers": {
    "ai-visibility": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/ai-visibility-mcp", "run", "ai-visibility-mcp"]
    }
  }
}
```

## Example session

```
> check_ai_bot_access(domain="bandcamp.com")

  summary: { total: 22, allowed: 13, disallowed: 9 }
  warnings: ["9/22 AI bots disallowed — site largely invisible to AI search"]
  blocked:  ["GPTBot", "ClaudeBot", "Google-Extended", "Bytespider",
             "CCBot", "Meta-ExternalAgent", "FacebookBot", "Amazonbot", "Diffbot"]

> audit_ai_visibility(domain="bandcamp.com")

  score: 49
  reasons:
    -36: 9 AI bots disallowed in robots.txt
    -10: no JSON-LD structured data
    -5:  no /sitemap.xml

> check_llm_mention(brand="Anthropic", query="Who makes the leading foundation AI models?")

  share_of_voice: 0.667
  by_model:
    perplexity/sonar          mentioned=true   citations=3
    openrouter/gpt-4o-mini    mentioned=true   citations=0
    openrouter/gemini-flash   mentioned=false  citations=0
  est_total_cost_usd: 0.00088
  daily_spend_usd:    0.00088 / $5.00 cap
```

## Security posture

This server makes outbound HTTP requests to caller-supplied domains and to LLM providers. v0.2 hardening:

- **SSRF guard.** All outbound HTTP refuses loopback, link-local (AWS / GCP / Azure metadata IPs), RFC1918, CGNAT, and IPv6 ULA addresses. Redirects are re-validated.
- **Daily spend cap.** LLM calls are gated by `MAX_DAILY_USD` (default $5.00), persisted to `~/.cache/ai-visibility-mcp/spend.json`. Loop-amplification can't drain your Perplexity / OpenRouter credits.
- **Per-call cost ceiling.** `MAX_COST_PER_CALL` (default $0.10) plus `LLM_MAX_OUTPUT_TOKENS` (default 1024) hard-bounds any single tool invocation.
- **No persistence of user content.** Nothing is logged to disk except the daily spend totals.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PERPLEXITY_API_KEY` | — | Required for Perplexity models in `check_llm_mention` |
| `OPENROUTER_API_KEY` | — | Required for OpenAI / Gemini / Claude via OpenRouter |
| `MAX_COST_PER_CALL` | `0.10` | USD ceiling per tool invocation |
| `MAX_DAILY_USD` | `5.00` | USD ceiling per UTC day, persisted |
| `LLM_MAX_OUTPUT_TOKENS` | `1024` | Hard cap on output tokens per LLM call |
| `AI_VISIBILITY_SPEND_FILE` | `~/.cache/ai-visibility-mcp/spend.json` | Override spend ledger location |

## Development

```bash
uv sync --extra dev
uv run pytest          # 24 tests
uv run ruff check .    # lint
```

## Status

v0.2 — security-hardened, 24/24 tests, smoke-verified against tealhq.com / bandcamp.com / anthropic.com. `track_changes` (persistent diff over time) deferred to v0.3.

## License

MIT.

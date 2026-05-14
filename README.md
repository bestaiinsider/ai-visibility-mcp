# ai-visibility-mcp

MCP server for auditing AI-bot visibility. Answers: can AI bots read this site, is the brand surfacing in LLM answers, is Cloudflare blocking AI search by accident.

## Tools

| Tool | Purpose |
|---|---|
| `check_ai_bot_access(domain)` | robots.txt + headers, per-bot allow/disallow, Cloudflare AI-defaults flag |
| `audit_ai_visibility(domain)` | composite report (robots + meta + structured data + sitemap) |
| `check_llm_mention(brand, query)` | does the brand surface in Perplexity / Tavily / OpenRouter LLM answers |
| `compare_competitors(domain, competitors[])` | side-by-side AI-visibility audit |

## Install

```bash
uv sync
cp .env.example .env  # fill from ~/Setup/credentials.md
```

## Run

```bash
# stdio (Claude Desktop / Claude Code)
uv run ai-visibility-mcp

# HTTP (remote agents)
uv run ai-visibility-mcp --http --port 8000
```

## Claude Desktop / Code config

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

## Tool details

### `check_ai_bot_access(domain)`
Fetches `robots.txt` and the root URL. Returns per-bot verdict for 22 known
AI user-agents (GPTBot, ClaudeBot, PerplexityBot, Google-Extended, Bytespider,
CCBot, etc.) plus a Cloudflare bot-challenge signal. Pure HTTP, no API keys
required.

### `audit_ai_visibility(domain)`
Composite report: bot access + on-page meta robots (incl. `noai` / `noimageai`),
JSON-LD structured data + schema types, sitemap.xml presence + URL count,
`llms.txt` / `llms-full.txt` presence. Emits a 0-100 score with explainable
deductions.

### `check_llm_mention(brand, query, aliases?, models?)`
Fans the same query out to multiple LLMs (default: Perplexity sonar, OpenAI
gpt-4o-mini, Gemini 2.0 Flash) and reports per-model mention, matched alias,
citations, tokens, est cost. Cost-capped via `MAX_COST_PER_CALL` env var.

Requires `PERPLEXITY_API_KEY` and/or `OPENROUTER_API_KEY` in `.env`.

### `compare_competitors(your_domain, competitor_domains[])`
Parallel `audit_ai_visibility` across N domains, returns ranked comparison
table (score, blocked bots, JSON-LD, llms.txt, sitemap size, Cloudflare).

## Status

v0.1 — all 4 tools live, unit-tested, smoke-verified against 3 real domains.
Persistence (`track_changes`) deferred to v0.2.


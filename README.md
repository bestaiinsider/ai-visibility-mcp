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

## Status

v0.1 — `check_ai_bot_access` only. See vault project note for roadmap.

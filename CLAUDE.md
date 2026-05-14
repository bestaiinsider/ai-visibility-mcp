# AI-Visibility MCP — Project Instructions

> Project-scoped CLAUDE.md. User-scope durable rules at `~/.claude/CLAUDE.md` still apply.
> Project tracker / lifecycle / decisions live in vault: `~/Documents/Vault/02 Projects/ai-visibility-mcp.md`

## What this builds

A paid Model Context Protocol server that exposes AI-search-visibility data: which AI bots can read a site, whether brands surface in LLM-generated answers, and whether Cloudflare's AI-bot defaults are accidentally blocking the site from AI search.

## Tools the MCP exposes (target API)

1. `check_ai_bot_access(domain)` — robots.txt + headers per-bot
2. `check_llm_mention(brand, query)` — does the brand surface in Perplexity / Tavily / OpenRouter LLM answers
3. `audit_ai_visibility(domain)` — composite report
4. `compare_competitors(your_domain, competitor_domains)` — side-by-side
5. `track_changes(domain)` — diff over time (v2, needs persistence)

## Stack

- **Language:** Python 3.10+ (FastMCP)
- **Package mgmt:** `uv`
- **Dependencies:** `fastmcp`, `httpx`, `python-dotenv`, `pyyaml`
- **External APIs (credentials in `~/Setup/credentials.md`):**
  - Perplexity (LLM-aware search)
  - Tavily (LLM-grounded web search)
  - OpenRouter (cross-model LLM checks: Claude, GPT, Gemini)
  - Firecrawl (page scrape for tag/structured-data audit)
- **Transport:** stdio + HTTP (FastMCP supports both)

## Build phase order

1. Skeleton + `check_ai_bot_access` (offline-ish, just HTTP requests)
2. `audit_ai_visibility` composite
3. `check_llm_mention` (uses Perplexity + OpenRouter)
4. `compare_competitors`
5. README + listing on Smithery / mcp.so / Glama (free)
6. Paid backend via MCPize (gated tools behind license key)
7. Direct customer-facing audit subscription (separate web app, reuses MCP backend)

## Test before claim

Three real domains for E2E tests:
- `tealhq.com` (known-AI-friendly)
- `bandcamp.com` (known to block aggressively)
- Whatever Kris's primary domain ends up being

Each tool must produce output against all three before being marked done.

## Project-specific rules

1. **Don't ship the paid tier before the free MVP works** — Smithery/mcp.so free listing first, then paid via MCPize.
2. **Open-source the MCP shim; keep paid backend closed.** The protocol layer is OSS for trust; the data/intelligence layer is the moat.
3. **No customer support ticket system until 5+ paying users.** Just an email address.
4. **Cron-friendly:** every tool must work in non-interactive mode (cron-callable for the downstream subscription product).
5. **Cost guard:** each tool call must enforce a max-API-cost ceiling (e.g., $0.10/call default).

## Distribution targets (Day 3)

- MCP Hive (paid)
- Smithery
- mcp.so
- Glama
- GitHub
- X thread, Product Hunt, r/ClaudeAI, IndieHackers, HN Show

## Credentials

Pull from `~/Setup/credentials.md` at install time. Write to `.env` (gitignored). Never commit values.

Required keys:
- `PERPLEXITY_API_KEY`
- `TAVILY_API_KEY`
- `OPENROUTER_API_KEY`
- `FIRECRAWL_API_KEY`
- `APIFY_API_TOKEN` (optional, for bulk audits)

## Done definition for v1

- All 4 tools work against 3 real domains
- README clear enough that a stranger can install + run in <5 min
- Listed on at least 2 of 4 MCP marketplaces
- One paying user OR strong "I'd pay for this" feedback signal

# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] — 2026-05-23

### Added

- `generate_robots_patch(domain, allow_bots?, deny_bots?, preserve_existing?)` — pure-rule robots.txt generator; no LLM call; opens access to all 22 known AI bots; preserves existing rules; detects Cloudflare WAF warning
- `generate_json_ld(url, page_type?, include_breadcrumbs?)` — Schema.org JSON-LD generator; auto-detects page type (Product, Article, Organization, FAQPage, SoftwareApplication, WebSite); calls gpt-4o-mini once; validates required/recommended fields; returns ready-to-paste `<script>` block
- `generate_llms_txt(domain, crawl_depth?, max_pages?)` — llms.txt generator per https://llmstxt.org/; crawls homepage + sitemap.xml; falls back to anchor-link extraction when sitemap absent; calls gpt-4o-mini once; validates format
- All three generators: SSRF-guarded, MAX_DAILY_USD cap, MAX_COST_PER_CALL ceiling
- 16 new tests (40 total, all passing)

### Changed

- Version bumped to 0.3.0
- README updated with audit-and-fix four-step loop, generator tools table

## [0.2.0] — 2026-05-15

### Added

- `check_llm_mention(brand, query, aliases?, models?)` — cross-model brand surfacing via Perplexity sonar + OpenRouter (gpt-4o-mini, Gemini 2.0 Flash)
- `compare_competitors(your_domain, competitor_domains[])` — parallel ranked audit
- SSRF guard: refuses loopback, link-local, RFC1918, CGNAT, IPv6 ULA; redirect re-validation
- Daily spend cap (`MAX_DAILY_USD`) + per-call ceiling (`MAX_COST_PER_CALL`)
- 24 tests

## [0.1.0] — 2026-05-10

### Added

- `check_ai_bot_access(domain)` — per-bot robots.txt + Cloudflare AI-default flag for 22 AI user-agents
- `audit_ai_visibility(domain)` — 0-100 composite score (robots, meta, JSON-LD, sitemap, llms.txt, SPA shell detection)
- FastMCP server with stdio + HTTP transport

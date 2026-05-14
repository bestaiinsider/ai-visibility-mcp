"""Known AI bot user-agents as of 2026-05."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bot:
    ua: str
    vendor: str
    purpose: str


KNOWN_AI_BOTS: tuple[Bot, ...] = (
    Bot("GPTBot", "OpenAI", "training"),
    Bot("ChatGPT-User", "OpenAI", "user-fetch"),
    Bot("OAI-SearchBot", "OpenAI", "search-index"),
    Bot("ClaudeBot", "Anthropic", "training"),
    Bot("Claude-User", "Anthropic", "user-fetch"),
    Bot("Claude-SearchBot", "Anthropic", "search-index"),
    Bot("anthropic-ai", "Anthropic", "legacy"),
    Bot("PerplexityBot", "Perplexity", "search-index"),
    Bot("Perplexity-User", "Perplexity", "user-fetch"),
    Bot("Google-Extended", "Google", "gemini-training"),
    Bot("GoogleOther", "Google", "research"),
    Bot("Applebot-Extended", "Apple", "training"),
    Bot("Bytespider", "ByteDance", "training"),
    Bot("CCBot", "Common Crawl", "open-dataset"),
    Bot("Meta-ExternalAgent", "Meta", "training"),
    Bot("FacebookBot", "Meta", "user-fetch"),
    Bot("Amazonbot", "Amazon", "alexa-llm"),
    Bot("DuckAssistBot", "DuckDuckGo", "assist"),
    Bot("cohere-ai", "Cohere", "training"),
    Bot("Diffbot", "Diffbot", "knowledge-graph"),
    Bot("YouBot", "You.com", "search-index"),
    Bot("MistralAI-User", "Mistral", "user-fetch"),
)

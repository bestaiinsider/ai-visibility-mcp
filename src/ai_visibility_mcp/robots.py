"""Minimal robots.txt parser focused on per-UA allow/disallow checks.

We don't use urllib.robotparser because it normalizes case and merges
records in ways that hide per-bot intent (which is the whole product).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Record:
    user_agents: list[str] = field(default_factory=list)
    allows: list[str] = field(default_factory=list)
    disallows: list[str] = field(default_factory=list)


def parse(text: str) -> list[Record]:
    records: list[Record] = []
    current: Record | None = None
    last_was_ua = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()

        if field_name == "user-agent":
            if current is None or not last_was_ua:
                current = Record()
                records.append(current)
            current.user_agents.append(value)
            last_was_ua = True
        elif field_name in ("allow", "disallow") and current is not None:
            (current.allows if field_name == "allow" else current.disallows).append(value)
            last_was_ua = False
        else:
            last_was_ua = False

    return records


def _path_matches(pattern: str, path: str) -> bool:
    """Google-style longest-match path with `*` and `$` wildcards."""
    if not pattern:
        return False
    if "*" not in pattern and "$" not in pattern:
        return path.startswith(pattern)
    end_anchor = pattern.endswith("$")
    pat = pattern[:-1] if end_anchor else pattern
    parts = pat.split("*")
    pos = 0
    for i, part in enumerate(parts):
        if i == 0:
            if not path.startswith(part):
                return False
            pos = len(part)
        else:
            idx = path.find(part, pos)
            if idx < 0:
                return False
            pos = idx + len(part)
    if end_anchor and pos != len(path):
        return False
    return True


def access_for(records: list[Record], user_agent: str, url_path: str = "/") -> str:
    """Returns 'allowed' | 'disallowed' | 'unspecified'.

    Resolution: pick the record whose UA matches `user_agent` most specifically.
    If no specific match, fall back to the `*` wildcard record.
    Within the chosen record, longest matching pattern wins; ties go to Allow.
    """
    ua_lower = user_agent.lower()
    specific: Record | None = None
    wildcard: Record | None = None

    for rec in records:
        for ua in rec.user_agents:
            ua_norm = ua.strip().lower()
            if ua_norm == "*":
                wildcard = rec
            elif ua_norm and ua_norm in ua_lower:
                specific = rec

    chosen = specific or wildcard
    if chosen is None:
        return "unspecified"
    if not chosen.allows and not chosen.disallows:
        return "unspecified"

    best_len = -1
    best_verdict = "allowed"
    for pattern in chosen.disallows:
        if pattern == "":
            continue
        if _path_matches(pattern, url_path) and len(pattern) > best_len:
            best_len = len(pattern)
            best_verdict = "disallowed"
    for pattern in chosen.allows:
        if _path_matches(pattern, url_path) and len(pattern) >= best_len:
            best_len = len(pattern)
            best_verdict = "allowed"

    if best_len < 0:
        if any(d == "" for d in chosen.disallows):
            return "allowed"
        return "allowed"
    return best_verdict


def normalize_domain(domain: str) -> str:
    if "://" not in domain:
        domain = "https://" + domain
    parts = urlsplit(domain)
    host = parts.netloc or parts.path
    return f"https://{host}"

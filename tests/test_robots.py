from ai_visibility_mcp.robots import access_for, parse, _path_matches


def test_parse_basic_records():
    text = """
    User-agent: GPTBot
    Disallow: /

    User-agent: *
    Disallow: /admin
    Allow: /
    """
    recs = parse(text)
    assert len(recs) == 2
    assert recs[0].user_agents == ["GPTBot"]
    assert recs[0].disallows == ["/"]
    assert recs[1].user_agents == ["*"]
    assert "/admin" in recs[1].disallows


def test_grouped_user_agents():
    text = """
    User-agent: GPTBot
    User-agent: ClaudeBot
    Disallow: /private
    """
    recs = parse(text)
    assert len(recs) == 1
    assert recs[0].user_agents == ["GPTBot", "ClaudeBot"]


def test_explicit_block_wins_over_wildcard():
    recs = parse("User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    assert access_for(recs, "GPTBot", "/") == "disallowed"
    assert access_for(recs, "PerplexityBot", "/") == "allowed"


def test_no_match_means_unspecified():
    recs = parse("")
    assert access_for(recs, "GPTBot", "/") == "unspecified"


def test_longest_match_wins():
    recs = parse("User-agent: *\nDisallow: /\nAllow: /public\n")
    assert access_for(recs, "GPTBot", "/public/page") == "allowed"
    assert access_for(recs, "GPTBot", "/private") == "disallowed"


def test_path_wildcards():
    assert _path_matches("/*.pdf$", "/file.pdf")
    assert not _path_matches("/*.pdf$", "/file.pdf.html")
    assert _path_matches("/api/*/v1", "/api/foo/v1")


def test_comments_and_blank_lines():
    text = """
    # comment
    User-agent: GPTBot  # inline
    Disallow: /secret

    # another comment
    """
    recs = parse(text)
    assert len(recs) == 1
    assert recs[0].disallows == ["/secret"]

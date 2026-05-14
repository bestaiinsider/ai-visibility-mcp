from ai_visibility_mcp.audit import interpret_meta_robots, parse_html


def test_parse_meta_robots():
    html = """
    <html><head>
      <title>Hello</title>
      <meta name="description" content="example site">
      <meta name="robots" content="noindex, nofollow">
      <meta name="GPTBot" content="noindex">
      <meta property="og:title" content="Hello OG">
    </head><body></body></html>
    """
    out = parse_html(html)
    assert out["title"] == "Hello"
    assert out["description"] == "example site"
    assert out["ai_meta_tags"]["robots"] == "noindex, nofollow"
    assert out["ai_meta_tags"]["gptbot"] == "noindex"
    assert out["open_graph"]["og:title"] == "Hello OG"


def test_parse_jsonld_blocks():
    html = '''
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Organization","name":"Acme"}
    </script>
    <script type="application/ld+json">
    {"@type":["WebPage","ItemPage"],"name":"page"}
    </script>
    '''
    out = parse_html(html)
    assert out["jsonld_count"] == 2
    assert out["jsonld_errors"] == []
    assert "Organization" in out["schema_types"]
    assert "WebPage" in out["schema_types"]
    assert "ItemPage" in out["schema_types"]


def test_jsonld_parse_error_is_captured():
    html = '<script type="application/ld+json">{not json}</script>'
    out = parse_html(html)
    assert out["jsonld_count"] == 0
    assert len(out["jsonld_errors"]) == 1


def test_interpret_meta_robots_flags():
    f = interpret_meta_robots("noindex, NoFollow, noai")
    assert f["noindex"] is True
    assert f["nofollow"] is True
    assert f["noai"] is True
    assert f["noimageai"] is False


def test_reversed_attribute_order():
    html = '<meta content="custom" name="x-test">'
    out = parse_html(html)
    assert out["ai_meta_tags"] == {}

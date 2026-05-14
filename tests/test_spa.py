from ai_visibility_mcp.audit import detect_spa_shell


def test_empty_spa_shell_detected():
    html = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    out = detect_spa_shell(html)
    assert out["likely_spa_shell"] is True
    assert out["visible_text_chars"] == 0


def test_server_rendered_not_flagged():
    html = "<html><body>" + ("<p>Real product copy explaining the value proposition. " * 30) + "</p></body></html>"
    out = detect_spa_shell(html)
    assert out["likely_spa_shell"] is False
    assert out["visible_text_chars"] > 500


def test_next_data_signal():
    html = '<html><body><div id="__next"></div><script id="__NEXT_DATA__" type="application/json">{}</script></body></html>'
    out = detect_spa_shell(html)
    assert out["js_app_signals"] >= 1


def test_empty_string_is_safe():
    out = detect_spa_shell("")
    assert out["likely_spa_shell"] is False

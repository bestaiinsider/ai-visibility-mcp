import pytest

from ai_visibility_mcp.llm import (
    DailyCapReached,
    assert_under_daily_cap,
    read_daily_spend,
    record_spend,
)
from ai_visibility_mcp.server import SSRFBlocked, _assert_public_url


def test_ssrf_blocks_aws_imds():
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://169.254.169.254/latest/meta-data/")


def test_ssrf_blocks_loopback():
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://127.0.0.1:8000/")
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://localhost/")


def test_ssrf_blocks_private_v4():
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://10.0.0.5/")
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://192.168.1.1/")
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://172.16.0.1/")


def test_ssrf_blocks_non_http_scheme():
    with pytest.raises(SSRFBlocked):
        _assert_public_url("file:///etc/passwd")
    with pytest.raises(SSRFBlocked):
        _assert_public_url("gopher://example.com/")


def test_ssrf_blocks_unresolvable_host():
    with pytest.raises(SSRFBlocked):
        _assert_public_url("http://nonexistent-host-12345.invalid/")


def test_ssrf_allows_public_host():
    _assert_public_url("https://example.com/")
    _assert_public_url("https://anthropic.com/robots.txt")


def test_daily_spend_records_and_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_VISIBILITY_SPEND_FILE", str(tmp_path / "spend.json"))
    monkeypatch.setenv("MAX_DAILY_USD", "0.05")
    assert read_daily_spend() == 0.0
    record_spend(0.03)
    assert abs(read_daily_spend() - 0.03) < 1e-9
    record_spend(0.01)
    assert abs(read_daily_spend() - 0.04) < 1e-9
    assert_under_daily_cap(0.005)  # 0.04 + 0.005 < 0.05 OK
    with pytest.raises(DailyCapReached):
        assert_under_daily_cap(0.02)  # 0.04 + 0.02 > 0.05


def test_spend_file_corruption_resets(tmp_path, monkeypatch):
    spend = tmp_path / "spend.json"
    spend.write_text("{not json")
    monkeypatch.setenv("AI_VISIBILITY_SPEND_FILE", str(spend))
    assert read_daily_spend() == 0.0

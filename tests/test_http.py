import pytest
import requests

from renewkit.http import TransientError, build_session, request


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    """按预设序列返回响应/抛异常，并记录调用次数。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_success_first_try():
    s = FakeSession([FakeResponse(200)])
    r = request(s, "GET", "https://x", backoff=(1, 2), sleep=lambda _: None)
    assert r.status_code == 200
    assert s.calls == 1


def test_5xx_retries_then_raises_transient():
    s = FakeSession([FakeResponse(522), FakeResponse(522), FakeResponse(503)])
    with pytest.raises(TransientError) as ei:
        request(s, "GET", "https://x", backoff=(0, 0), sleep=lambda _: None)
    assert s.calls == 3
    assert ei.value.status_code == 503


def test_5xx_then_recovers():
    s = FakeSession([FakeResponse(522), FakeResponse(200)])
    r = request(s, "GET", "https://x", backoff=(0, 0), sleep=lambda _: None)
    assert r.status_code == 200
    assert s.calls == 2


def test_connection_error_is_transient():
    s = FakeSession([requests.ConnectionError("boom"), requests.ConnectionError("boom")])
    with pytest.raises(TransientError):
        request(s, "GET", "https://x", backoff=(0,), sleep=lambda _: None)
    assert s.calls == 2


def test_404_is_not_retried():
    """确定性错误不该重试。"""
    s = FakeSession([FakeResponse(404)])
    r = request(s, "GET", "https://x", backoff=(0, 0), sleep=lambda _: None)
    assert r.status_code == 404
    assert s.calls == 1


def test_build_session_has_default_ua():
    s = build_session()
    assert "Mozilla" in s.headers["User-Agent"]


# ── summarize_http_failure ────────────────────────────────────────────────

def test_summarize_extracts_title_from_html_error_page():
    """CF / nginx 的整页 HTML 只留 <title>，不要把通知撑爆。"""
    from renewkit.http import summarize_http_failure
    page = ("<!DOCTYPE html><html><head><title>502 Bad Gateway</title></head>"
            "<body><h1>502 Bad Gateway</h1>" + "<p>x</p>" * 500 + "</body></html>")
    out = summarize_http_failure(502, page)
    assert out == "HTTP 502: 502 Bad Gateway"
    assert len(out) < 60


def test_summarize_unescapes_and_collapses_title():
    from renewkit.http import summarize_http_failure
    page = "<html><title>Just a moment... &amp; more\n  text</title></html>"
    assert summarize_http_failure(403, page) == "HTTP 403: Just a moment... & more text"


def test_summarize_plain_text_body():
    from renewkit.http import summarize_http_failure
    assert summarize_http_failure(500, "  boom\n\n  happened ") == "HTTP 500: boom happened"


def test_summarize_html_without_title():
    from renewkit.http import summarize_http_failure
    assert summarize_http_failure(522, "<html><body>oops</body></html>") == \
        "HTTP 522: HTML 错误页（无 title）"


def test_summarize_empty_body_returns_status_only():
    from renewkit.http import summarize_http_failure
    assert summarize_http_failure(503, "") == "HTTP 503"
    assert summarize_http_failure(503, None) == "HTTP 503"


def test_summarize_truncates_long_body():
    from renewkit.http import HTTP_ERROR_DETAIL_LIMIT, summarize_http_failure
    out = summarize_http_failure(500, "A" * 5000)
    assert len(out) <= len("HTTP 500: ") + HTTP_ERROR_DETAIL_LIMIT
    assert out.endswith("...")

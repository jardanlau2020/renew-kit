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

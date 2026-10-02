"""notify 模块测试：不发真请求，靠 monkeypatch urlopen。"""
import json
import urllib.parse

import pytest

from renewkit import notify


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status = status
        self._body = json.dumps(body if body is not None else {"ok": True}).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def tg_env(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "123")


def _capture(monkeypatch, resp):
    """拦截 urlopen，返回 (captured_request_holder, )。"""
    box = {}

    def fake_urlopen(req, timeout=None):
        box["req"] = req
        box["timeout"] = timeout
        return resp

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    return box


def test_missing_config_returns_false_and_does_not_raise(monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.send("hi") is False


def test_send_uses_post_with_form_body(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    assert notify.send("hello world") is True

    req = box["req"]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert req.headers["Content-type"] == "application/x-www-form-urlencoded"
    # 参数在 body 里，不在 URL 上
    assert "?" not in req.full_url
    fields = urllib.parse.parse_qs(req.data.decode())
    assert fields["chat_id"] == ["123"]
    assert fields["text"] == ["hello world"]
    assert "parse_mode" not in fields


def test_send_passes_parse_mode_when_given(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    assert notify.send("<b>x</b>", parse_mode="HTML") is True
    fields = urllib.parse.parse_qs(box["req"].data.decode())
    assert fields["parse_mode"] == ["HTML"]
    assert fields["text"] == ["<b>x</b>"]


def test_send_truncates_over_limit(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    notify.send("z" * (notify.MESSAGE_LIMIT + 500))
    text = urllib.parse.parse_qs(box["req"].data.decode())["text"][0]
    assert len(text) == notify.MESSAGE_LIMIT
    assert text.endswith(notify.TRUNCATION_SUFFIX)


def test_send_returns_false_on_api_error_body(tg_env, monkeypatch):
    _capture(monkeypatch, FakeResp(400, {"ok": False, "description": "bad parse_mode"}))
    assert notify.send("x", parse_mode="HTML") is False


def test_send_returns_false_on_network_exception(tg_env, monkeypatch):
    def boom(req, timeout=None):
        raise OSError("no route to host")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send("x") is False

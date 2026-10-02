"""notify 模块测试：不发真请求，靠 monkeypatch urlopen。"""
import contextlib
import io
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
    # DRY_RUN 会短路掉整个发送路径 —— 不显式清掉的话，跑测试的机器上只要有
    # DRY_RUN=1（CI 演练场景很常见），一大半用例会莫名其妙地失败。
    monkeypatch.delenv("DRY_RUN", raising=False)


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
    monkeypatch.delenv("DRY_RUN", raising=False)
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


# ── 内联键盘（v0.5.0） ────────────────────────────────────────────────


def _fields(box):
    return urllib.parse.parse_qs(box["req"].data.decode())


def test_send_without_buttons_has_no_reply_markup(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    notify.send("x")
    assert "reply_markup" not in _fields(box)


def test_send_flat_buttons_become_one_row(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    buttons = [
        {"text": "🔓 去續期", "url": "https://openworld.eu.org/x"},
        {"text": "📄 面板", "url": "https://openworld.eu.org/p"},
    ]
    assert notify.send("去续期", buttons=buttons) is True

    markup = json.loads(_fields(box)["reply_markup"][0])
    assert markup == {"inline_keyboard": [buttons]}


def test_send_nested_buttons_keep_rows(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    rows = [
        [{"text": "A", "url": "https://a"}],
        [{"text": "B", "url": "https://b"}, {"text": "C", "url": "https://c"}],
    ]
    notify.send("x", buttons=rows)
    markup = json.loads(_fields(box)["reply_markup"][0])
    assert markup["inline_keyboard"] == rows


def test_send_buttons_and_parse_mode_coexist(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    notify.send("<b>hi</b>", parse_mode="HTML",
                buttons=[{"text": "T", "url": "https://t"}])
    fields = _fields(box)
    assert fields["parse_mode"] == ["HTML"]
    assert fields["text"] == ["<b>hi</b>"]
    markup = json.loads(fields["reply_markup"][0])
    assert markup["inline_keyboard"] == [[{"text": "T", "url": "https://t"}]]


def test_send_buttons_keep_non_ascii_readable(tg_env, monkeypatch):
    """ensure_ascii=False：URL 里带中文/emoji 也别被转成 \\uXXXX 一堆。"""
    box = _capture(monkeypatch, FakeResp())
    notify.send("x", buttons=[{"text": "🔓 去續期", "url": "https://a"}])
    raw = _fields(box)["reply_markup"][0]
    assert "🔓" in raw
    assert "\\u" not in raw


def test_send_empty_buttons_ignored(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    notify.send("x", buttons=[])
    assert "reply_markup" not in _fields(box)


def test_send_drops_button_without_text(tg_env, monkeypatch):
    """缺 text 的按钮会被 Telegram 400 掉，宁可丢掉它也别让整条通知发不出去。"""
    box = _capture(monkeypatch, FakeResp())
    notify.send("x", buttons=[{"url": "https://no-text"}, {"text": "OK", "url": "https://ok"}])
    markup = json.loads(_fields(box)["reply_markup"][0])
    assert markup["inline_keyboard"] == [[{"text": "OK", "url": "https://ok"}]]


def test_send_all_buttons_invalid_omits_markup(tg_env, monkeypatch):
    box = _capture(monkeypatch, FakeResp())
    notify.send("x", buttons=[{"url": "https://a"}, "not-a-dict"])
    assert "reply_markup" not in _fields(box)


def test_build_keyboard_falsy_returns_empty():
    assert notify.build_keyboard(None) == []
    assert notify.build_keyboard([]) == []


def test_build_keyboard_does_not_mutate_input():
    src = [{"text": "A", "url": "https://a"}]
    rows = notify.build_keyboard(src)
    rows[0][0]["text"] = "MUTATED"
    assert src[0]["text"] == "A"


# ── DRY_RUN 演练（v0.5.2） ───────────────────────────────────────────
#
# 这里刻意不用 capsys / parametrize：.verify/run_tests_offline.py 是个最小
# 替身 runner（给没装 pytest 的 Windows 环境兜底），只认 monkeypatch 和
# 简单 fixture。自带一个捕获上下文就能两边都跑。


@contextlib.contextmanager
def _stdout():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield buf


def _forbid_network(monkeypatch):
    """任何网络访问都算失败：演练模式下一个字节都不该发出去。"""

    def boom(req, timeout=None):
        raise AssertionError(f"演练模式竟然发了请求: {req.full_url}")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)


def test_dry_run_prints_preview_and_sends_nothing(tg_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    _forbid_network(monkeypatch)

    with _stdout() as buf:
        assert notify.send("hello dry") is False
    out = buf.getvalue()
    assert "DRY_RUN" in out
    assert "hello dry" in out


def test_dry_run_truthy_variants_gate_sending(tg_env, monkeypatch):
    _forbid_network(monkeypatch)
    for value in ("1", "true", "TRUE", "yes", "on", "y"):
        monkeypatch.setenv("DRY_RUN", value)
        with _stdout() as buf:
            notify.send("x")
        assert "DRY_RUN" in buf.getvalue(), f"DRY_RUN={value!r} 没被当成真值"


def test_falsy_dry_run_still_sends(tg_env, monkeypatch):
    for value in ("", "0", "false", "no", "off"):
        monkeypatch.setenv("DRY_RUN", value)
        box = _capture(monkeypatch, FakeResp())
        assert notify.send("real") is True, f"DRY_RUN={value!r} 不该拦发送"
        assert box["req"].full_url.endswith("/sendMessage")


def test_dry_run_previews_inline_buttons(tg_env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "1")
    _forbid_network(monkeypatch)
    with _stdout() as buf:
        notify.send("去续期", buttons=[{"text": "🔓 去續期", "url": "https://a"}])
    out = buf.getvalue()
    assert "内联按钮" in out
    assert "🔓 去續期" in out
    assert "https://a" in out


def test_dry_run_preview_is_truncated_like_the_real_thing(tg_env, monkeypatch):
    """预览要是原样打全文，就失去「看到的就是会发出去的那条」的意义。"""
    monkeypatch.setenv("DRY_RUN", "1")
    _forbid_network(monkeypatch)
    with _stdout() as buf:
        notify.send("z" * (notify.MESSAGE_LIMIT + 500))
    out = buf.getvalue()
    assert notify.TRUNCATION_SUFFIX in out
    assert len(out) < notify.MESSAGE_LIMIT + 200


def test_dry_run_without_config_still_previews(monkeypatch):
    """演练 + 没配 TG：仍要把内容打出来，方便无 secret 的 CI 验排版。"""
    monkeypatch.setenv("DRY_RUN", "1")
    for name in ("TG_BOT_TOKEN", "TG_CHAT_ID", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    _forbid_network(monkeypatch)

    with _stdout() as buf:
        assert notify.send("预览我") is False
    out = buf.getvalue()
    assert "预览我" in out
    assert "未配置" in out


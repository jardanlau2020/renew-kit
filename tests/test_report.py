import contextlib
import io

from renewkit.outcome import Outcome
from renewkit.report import RenewReport, TargetResult


def test_empty_report():
    r = RenewReport("Host-Ship")
    assert r.exit_code == 0
    assert "未发现服务器实例" in r.render()


def test_transient_does_not_fail_job():
    """核心回归：上游 522 不能让 job 标红。"""
    r = RenewReport("MonkeyBytes")
    r.add("monkey-1", Outcome.TRANSIENT, detail="面板 522")
    assert r.exit_code == 0
    assert r.worst is Outcome.TRANSIENT
    text = r.render()
    assert "上游暂不可用" in text


def test_failed_makes_job_red():
    r = RenewReport("X")
    r.add("srv", Outcome.FAILED, detail="登录失败")
    assert r.exit_code == 1
    assert "需要人工处理" in r.render()


def test_renewed_report_two_lines_per_target():
    r = RenewReport("Host-Ship")
    r.add("srv-a", Outcome.RENEWED, expire=30)
    lines = TargetResult("srv-a", Outcome.RENEWED, expire=30).lines()
    assert len(lines) == 2
    assert lines[0].startswith("✅")
    assert "30 天后" in lines[0]


def test_counts():
    r = RenewReport("X")
    r.add("a", Outcome.RENEWED)
    r.add("b", Outcome.RENEWED)
    r.add("c", Outcome.SKIPPED)
    assert r.counts() == {"renewed": 2, "skipped": 1}


def test_skipped_with_day_count_reads_naturally():
    """回归：renewal=27（剩余天数）不能渲染成「27 天后 到期」。"""
    lines = TargetResult("srv", Outcome.SKIPPED, expire=27).lines()
    assert "状态良好（剩 27 天）" in lines[0]
    assert "27 天后 到期" not in lines[1]


def test_skipped_day_count_not_repeated_on_second_line():
    """回归（v0.5.1）：天数只出现在第一行，第二行不再复读。

    线上原本渲染成
        🟢 srv · 状态良好（剩 12 天）
        ℹ️ 剩 12 天 · 未到续期窗口
    第一行已经说了天数，第二行再说一遍纯属噪音。
    """
    text = "\n".join(TargetResult("srv", Outcome.SKIPPED, expire=12).lines())
    assert text.count("剩 12 天") == 1, text
    assert text.endswith("ℹ️ 未到续期窗口"), text


def test_skipped_detail_still_shown_after_day_count():
    """去掉复读不能顺手把 detail 也吞掉。"""
    lines = TargetResult("srv", Outcome.SKIPPED, expire=12,
                         detail="伺服器已重啟").lines()
    assert "剩 12 天" in lines[0]
    assert lines[1] == "ℹ️ 伺服器已重啟"


def test_skipped_with_timestamp_reads_as_date():
    lines = TargetResult("srv", Outcome.SKIPPED, expire="2026-10-31T12:00:00").lines()
    assert "10-31 12:00 到期" in lines[1]


def test_unknown_renders_distinctly_and_does_not_fail_job():
    r = RenewReport("katabump")
    r.add("acct", Outcome.UNKNOWN, detail="未检测到明确提示")
    assert r.exit_code == 0
    text = r.render()
    assert "结果未确认" in text
    assert "❓" in text


def test_shorten_flattens_and_truncates():
    from renewkit.report import shorten
    assert shorten("a\n  b\tc") == "a b c"
    assert shorten("x" * 100, limit=10) == "x" * 9 + "…"


def test_shorten_accepts_non_str():
    """回归：在 except 块里写 shorten(exc) 曾在异常处理路径上二次抛
    AttributeError，导致报告和 TG 都发不出去。"""
    from renewkit.report import shorten
    assert shorten(RuntimeError("chrome not found")) == "chrome not found"
    assert shorten(None) == ""
    assert shorten(123) == "123"


def test_custom_renderer_takes_over_output_but_keeps_exit_code():
    """renderer 钩子：排版归调用方，结果语义/退出码仍归 renewkit。

    用于那些已经手工调好通知格式的仓库（如 Aut0-Renew-B0th0sting02）。
    """
    def my_format(report):
        return "MY-FORMAT:" + ",".join(r.outcome.value for r in report.results)

    r = RenewReport("bot-hosting", renderer=my_format)
    r.add("acct", Outcome.RENEWED)
    r.add("acct2", Outcome.TRANSIENT)
    assert r.render() == "MY-FORMAT:renewed,transient"
    # 上游故障不算失败
    assert r.exit_code == 0

    r.add("acct3", Outcome.FAILED)
    assert r.render() == "MY-FORMAT:renewed,transient,failed"
    assert r.exit_code == 1


def test_no_renderer_keeps_default_layout():
    r = RenewReport("svc")
    r.add("a", Outcome.RENEWED)
    assert r.render().startswith("【svc】")


# ── finish() 与演练开关（v0.5.2） ───────────────────────────────────


def _forbid_network(monkeypatch):
    from renewkit import notify

    def boom(req, timeout=None):
        raise AssertionError(f"演练模式竟然发了请求: {req.full_url}")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)


def test_finish_returns_exit_code_without_sending(monkeypatch):
    """notify_tg=False 时只打印，不碰网络。"""
    _forbid_network(monkeypatch)
    r = RenewReport("svc")
    r.add("a", Outcome.RENEWED, expire=30)
    assert r.finish(notify_tg=False) == 0
    assert r.exit_code == 0

    r2 = RenewReport("svc")
    r2.add("a", Outcome.FAILED, detail="x")
    assert r2.finish(notify_tg=False) == 1


def test_finish_honours_dry_run(monkeypatch):
    """回归：DRY_RUN=1 时 finish() 不能真发 TG。

    kit 里 env.dry_run() 原本是没人调用的死代码，RenewReport.finish() 走
    notify.send() 会绕过各仓库自己抄的演练闸门 —— 演练把真通知发出去。
    闸门收进 notify.send() 之后这条路径才被真正覆盖。
    """
    monkeypatch.setenv("TG_BOT_TOKEN", "TOKEN")
    monkeypatch.setenv("TG_CHAT_ID", "123")
    monkeypatch.setenv("DRY_RUN", "1")
    _forbid_network(monkeypatch)

    r = RenewReport("svc")
    r.add("a", Outcome.FAILED, expire=5, detail="需人工")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert r.finish() == 1      # 演练不影响退出码
    out = buf.getvalue()
    assert "DRY_RUN" in out
    assert "续期未完成" in out        # 报告本体照打印

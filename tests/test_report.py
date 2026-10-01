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
    assert "剩 27 天" in lines[1]
    assert "27 天后 到期" not in lines[1]


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

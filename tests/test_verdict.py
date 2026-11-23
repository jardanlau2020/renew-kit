"""判据文件（renewkit.verdict）的行为测试。

聚焦三件事：
    1. record/read 能往返，中文 detail 不被写花；
    2. 退出码语义 —— 「run 绿」只等于「没有坏结果」，不等于续期成功；
    3. 缺判据时的两种口径：默认红灯（不能证明续上了），--allow-missing 才放行。

不依赖 pytest 的 tmp_path fixture —— 用 tempfile 自建，好让离线替身也能跑。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from renewkit import Outcome, classify_progress, verdict


def _tmpdir():
    return Path(tempfile.mkdtemp(prefix="renewkit-verdict-"))


def _clean(monkeypatch, tmp: Path):
    """把判据文件钉在临时目录，并清掉可能干扰的环境变量。"""
    monkeypatch.setenv(verdict.ENV_FILE, str(tmp / verdict.DEFAULT_FILE))
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    monkeypatch.delenv(verdict.ENV_ALLOW_MISSING, raising=False)


def test_record_read_roundtrip_keeps_chinese(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    p = verdict.record(Outcome.NOT_ADVANCED, "续前 5 天 → 续后 5 天")
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["outcome"] == "not_advanced"
    assert data["is_error"] is True
    assert data["detail"] == "续前 5 天 → 续后 5 天"      # 没被写成 ???? 
    assert verdict.read() is Outcome.NOT_ADVANCED


def test_record_accepts_plain_string(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    verdict.record("renewed", "站方自报 renewed")
    assert verdict.read() is Outcome.RENEWED


def test_record_writes_github_env_once(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    env_file = tmp / "github_env"
    monkeypatch.setenv("GITHUB_ENV", str(env_file))
    verdict.record(Outcome.NO_TARGET, "账号下 0 个服务")
    assert "RENEWKIT_OUTCOME=no_target" in env_file.read_text(encoding="utf-8")


# ── 退出码语义：这一层就是假绿的根 ──────────────────────────────


def test_renewed_gives_green(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    verdict.record(Outcome.RENEWED, "13 → 14 天")
    assert verdict.main([]) == 0


def test_not_advanced_turns_job_red(monkeypatch):
    """回归：orihost-renew run 45000404309「0/0 台成功续期」以前也 exit 0。"""
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    verdict.record(Outcome.NOT_ADVANCED, "完成：0/0 台成功续期")
    assert verdict.main([]) == 1


def test_no_target_turns_job_red(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    verdict.record(Outcome.NO_TARGET)
    assert verdict.main([]) == 1


def test_unverified_results_stay_green(monkeypatch):
    """UNKNOWN / TRANSIENT / SKIPPED 不标红 —— 缺结果不等于坏结果。"""
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    for o in (Outcome.UNKNOWN, Outcome.TRANSIENT, Outcome.SKIPPED, Outcome.ALREADY_MAX):
        verdict.record(o)
        assert verdict.main([]) == 0, o


def test_missing_verdict_is_red_by_default(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    assert verdict.read() is None
    assert verdict.main([]) == 1


def test_allow_missing_via_flag_and_env(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    assert verdict.main(["--allow-missing"]) == 0
    monkeypatch.setenv(verdict.ENV_ALLOW_MISSING, "true")
    assert verdict.main([]) == 0


def test_damaged_verdict_file_is_red(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    verdict.filename().write_text("{ 不是 json", encoding="utf-8")
    assert verdict.read() is None
    assert verdict.main([]) == 1

    verdict.filename().write_text('{"outcome": "??"}', encoding="utf-8")
    assert verdict.read() is None
    assert verdict.main([]) == 1


def test_path_precedence(monkeypatch):
    tmp = _tmpdir()
    _clean(monkeypatch, tmp)
    assert verdict.filename("a/b.json").name == "b.json"
    assert verdict.filename().name == verdict.DEFAULT_FILE


# ── classify_progress：判据本体 ────────────────────────────────


def test_classify_progress_matches_real_runs():
    """两条真实日志：h0stship 13→14 是真好，orihost 0/0 是假绿。"""
    assert classify_progress(13, 14) is Outcome.RENEWED
    assert classify_progress(5, 5) is Outcome.NOT_ADVANCED
    assert classify_progress(5, 4) is Outcome.FAILED
    assert classify_progress(None, 14) is Outcome.UNKNOWN
    assert classify_progress(5, None) is Outcome.UNKNOWN
    assert classify_progress("5", "6") is Outcome.RENEWED      # 面板给的是字符串
    assert classify_progress("x", "6") is Outcome.UNKNOWN


def test_not_advanced_and_no_target_are_errors():
    assert Outcome.NOT_ADVANCED.is_error is True
    assert Outcome.NO_TARGET.is_error is True
    assert Outcome.NOT_ADVANCED.exit_code == 1
    assert Outcome.NO_TARGET.exit_code == 1
    # 老的「不算错」那几种不能被这次改动带红
    for o in (Outcome.RENEWED, Outcome.SKIPPED, Outcome.ALREADY_MAX,
              Outcome.UNKNOWN, Outcome.TRANSIENT):
        assert o.is_error is False

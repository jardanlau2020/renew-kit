"""判据文件：让 composite action 自己判断「这一轮到底续上没有」。

为什么在 renew-kit 里自写（而不是抄上游）
------------------------------------------
2026-11-23 逐行核过 eooce 那批上游（`katabump-renew` / `Auto-Renew-Bothosting` /
`Auto-Renew-MWS`）：三份 `app.py` 全文 **0 处** `sys.exit(非0)` 与续期结果有关
（Bothosting 唯一那处是「缺 EMAIL/PASSWORD 就退出」的配置守卫），没有
`raise`、没有 `GITHUB_OUTPUT`、没有 `GITHUB_STEP_SUMMARY`，续期成没成只落在
Telegram 文案上。上游 `renew.yml` 也是每仓自带一份，压根没有共用 action 层。
按内容搜过的关键词：`sys.exit`、`exit(`、`GITHUB_OUTPUT`、`GITHUB_STEP_SUMMARY`、
`renewed`、`can't renew`、`in:file "katabump"`、`repo user:eooce renew`。
结论：**上游没有可抄的退出码语义**，这一层只能自写。

病根
----
调用方脚本 exit 0 就让 run 变绿 —— 而「点过续期按钮」和「到期日真的推进」是
两回事。于是出现假绿：run 天天绿，服务照样到期（orihost-renew run 45000404309
「完成：0/0 台成功续期」却 exit 0；katabump-renew run 43559040647 日志写着
「登录失败，终止」也照样绿）。

写法
----
脚本收尾时落一次判据，action 里新增的收口步骤读同一个文件：

    from renewkit import Outcome, classify_progress, verdict

    verdict.record(classify_progress(before_days, after_days),
                   f"续前 {before_days} 天 → 续后 {after_days} 天")

    # 或者按业务直接给结论：
    verdict.record(Outcome.NOT_ADVANCED, "点过续期按钮，到期日未推进")

文件路径取环境变量 ``RENEWKIT_OUTCOME_FILE``，默认 ``renewkit-outcome.json``
（相对 action 的 working-directory）。CLI 退出码就是 job 该有的颜色：

    python -m renewkit.verdict [--allow-missing]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .outcome import Outcome

#: 判据文件名（相对 working-directory）
DEFAULT_FILE = "renewkit-outcome.json"
#: 覆盖判据文件路径的环境变量
ENV_FILE = "RENEWKIT_OUTCOME_FILE"
#: 允许「脚本没落判据」时维持原结论（"1"/"true"/"yes"）
ENV_ALLOW_MISSING = "RENEWKIT_ALLOW_MISSING"

_TRUTHY = ("1", "true", "yes", "on")


def filename(file: str | os.PathLike[str] | None = None) -> Path:
    """判据文件的路径。参数 > 环境变量 > 默认名。"""
    if file is None:
        file = os.environ.get(ENV_FILE) or DEFAULT_FILE
    return Path(file)


def record(outcome: Outcome | str, detail: str = "", *,
           file: str | os.PathLike[str] | None = None) -> Path:
    """落判据：写文件 + 打日志 +（在 Actions 里）写一份到 $GITHUB_ENV。

    故意「先写文件、再让 action 去读」，而不是直接在脚本里 ``sys.exit``：
    一是脚本退出码要留给脚本自己的异常处理，二是收口逻辑只有一处，
    免得五个仓库各写一遍、又各错一遍。
    """
    if not isinstance(outcome, Outcome):
        outcome = Outcome(str(outcome))
    path = filename(file)
    payload = {
        "outcome": outcome.value,
        "detail": str(detail or ""),
        "is_error": outcome.is_error,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError as exc:            # 写不进去就直说，别让收口步骤再去猜
        print(f"⚠️ 无法写入判据文件 {path}: {exc}", flush=True)
        raise

    line = f"{outcome.icon} renewkit 判据: {outcome.value}"
    if detail:
        line += f" · {detail}"
    print(line, flush=True)

    env_file = os.environ.get("GITHUB_ENV")
    if env_file:
        try:
            with open(env_file, "a", encoding="utf-8") as fh:
                fh.write(f"RENEWKIT_OUTCOME={outcome.value}\n")
        except OSError:
            pass                      # 只为排障好看，写不进去不影响判据
    return path


def load(file: str | os.PathLike[str] | None = None) -> dict | None:
    """读判据原始内容；文件不存在 / 不是 JSON 一律 None。"""
    try:
        data = json.loads(filename(file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read(file: str | os.PathLike[str] | None = None) -> Outcome | None:
    """读判据结果；没有文件或取值不认识一律 None。"""
    data = load(file)
    if data is None:
        return None
    try:
        return Outcome(str(data.get("outcome", "")).strip())
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    """收口入口。返回 job 该有的退出码：0 = 可以绿，1 = 必须红。"""
    args = list(sys.argv[1:] if argv is None else argv)
    allow_missing = ("--allow-missing" in args
                     or os.environ.get(ENV_ALLOW_MISSING, "").strip().lower() in _TRUTHY)
    path = filename()
    data = load(path)

    if data is None:
        if allow_missing:
            print(f"ℹ️ 未找到判据文件 {path}（已允许缺省）→ 跳过收口，维持脚本自身结论",
                  flush=True)
            return 0
        print(f"::error::未找到判据文件 {path} —— 脚本没有报告续期结果，"
              "「没失败」不等于「续上了」，本轮不能算成功。"
              "要交回脚本自己决定就设 allow-missing-outcome: true，"
              "或在脚本收尾调 renewkit.verdict.record() 落判据。", flush=True)
        return 1

    try:
        outcome = Outcome(str(data.get("outcome", "")).strip())
    except ValueError:
        print(f"::error::判据文件 {path} 的 outcome 取值无法识别：{data.get('outcome')!r}",
              flush=True)
        return 1

    detail = str(data.get("detail") or "")
    print(f"{outcome.icon} 续期判据: {outcome.value}" + (f" · {detail}" if detail else ""),
          flush=True)
    if outcome.is_error:
        print(f"::error::续期判据为 {outcome.value} —— 本轮不算续期成功"
              + (f"：{detail}" if detail else ""), flush=True)
        return 1
    return 0


if __name__ == "__main__":        # pragma: no cover - CLI 入口
    sys.exit(main())

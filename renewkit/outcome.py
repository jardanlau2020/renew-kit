"""结果分类。

统一「成功 / 跳过 / 已满 / 结果未确认 / 上游故障 / 未推进 / 无对象 / 真失败」
这几种语义，避免各仓库各写一套判断，出现把上游 5xx 误判成脚本失败的情况。

v0.6.0 起把「假绿」也收进这里：点了续期按钮、面板也回了 204，但到期日一天
没动，这属于 NOT_ADVANCED —— 以前这种一律 exit 0，run 天天绿、服务照样到期。
"""
from __future__ import annotations

from enum import Enum


class Outcome(str, Enum):
    RENEWED = "renewed"              # 本次确实续期成功（到期日/天数真的推进了）
    SKIPPED = "skipped"              # 未到续期窗口，无需操作（正常）
    ALREADY_MAX = "already_max"      # 面板已到上限，无需再续（正常）
    UNKNOWN = "unknown"              # 操作已执行但读不到明确结果，需人工留意
    TRANSIENT = "transient"          # 上游故障（5xx / 连不上 / 超时），重试后仍失败 -> 不算脚本错
    NOT_ADVANCED = "not_advanced"    # 点过续期，但到期日/剩余天数没有推进 -> 不算成功（假绿）
    NO_TARGET = "no_target"          # 登录成功，但账号下 0 个可续对象 -> 要么停用要么排查
    FAILED = "failed"                # 真失败，需要人工介入

    @property
    def is_error(self) -> bool:
        """是否应让 job 标红。

        只有「确定性的坏结果」算错：真失败、点过却没推进、一个可续对象都没有。
        上游 5xx（TRANSIENT）、未到窗口（SKIPPED）、读不到结果（UNKNOWN）仍算绿 ——
        它们不值得半夜叫人手起来看。
        """
        return self in _ERROR_OUTCOMES

    @property
    def exit_code(self) -> int:
        return 1 if self.is_error else 0

    @property
    def icon(self) -> str:
        return {
            Outcome.RENEWED: "✅",
            Outcome.SKIPPED: "🟢",
            Outcome.ALREADY_MAX: "⏭️",
            Outcome.UNKNOWN: "❓",
            Outcome.TRANSIENT: "🌐",
            Outcome.NOT_ADVANCED: "❗",
            Outcome.NO_TARGET: "🈳",
            Outcome.FAILED: "🚨",
        }[self]


#: 会让 job 标红的结果
_ERROR_OUTCOMES = frozenset({Outcome.FAILED, Outcome.NOT_ADVANCED, Outcome.NO_TARGET})


#: 上游临时故障对应的 HTTP 状态码
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})


def classify_status(status_code: int) -> Outcome:
    """按 HTTP 状态码粗分类。200/204 视为 RENEWED，5xx/429 视为 TRANSIENT。

    ⚠️ 别拿它当最终判据：200/204 只说明「站方收下了这个请求」，
    不代表到期日推进了。落判据请用 ``classify_progress()`` 比对前后到期天数，
    或自己确认按钮/接口语义后再定 Outcome。
    """
    if status_code in (200, 201, 204):
        return Outcome.RENEWED
    if status_code in TRANSIENT_STATUS or status_code == 0:
        return Outcome.TRANSIENT
    return Outcome.FAILED


def classify_progress(before, after) -> Outcome:
    """按「到期日有没有真的推进」定结果 —— 假绿的正面判据。

    before / after 是续期动作前后的剩余天数（或同一个口径下的到期时间），
    调用方保证两者同源同单位：

        after > before  -> RENEWED       真的续上了
        after == before -> NOT_ADVANCED  点过、也回 204，但天数没动 -> 标红
        after <  before -> FAILED        天数倒退，面板数据异常，人工看
        任一为 None     -> UNKNOWN       读不到 -> 不猜，也不标红

    实证背景：h0stship-renew-02 run 45181288610 是 13 -> 14 真推进；
    orihost-renew run 45000404309 是「完成：0/0 台成功续期」却 exit 0。
    两者光看 HTTP 状态码完全一样，只能比天数。
    """
    if before is None or after is None:
        return Outcome.UNKNOWN
    try:
        b, a = float(before), float(after)
    except (TypeError, ValueError):
        return Outcome.UNKNOWN
    if a > b:
        return Outcome.RENEWED
    if a == b:
        return Outcome.NOT_ADVANCED
    return Outcome.FAILED

"""结果分类。

统一「成功 / 跳过 / 已满 / 上游故障 / 真失败」这五种语义，
避免各仓库各写一套判断，出现把上游 5xx 误判成脚本失败的情况。
"""
from __future__ import annotations

from enum import Enum


class Outcome(str, Enum):
    RENEWED = "renewed"          # 本次确实续期成功
    SKIPPED = "skipped"          # 未到续期窗口，无需操作（正常）
    ALREADY_MAX = "already_max"  # 面板已到上限，无需再续（正常）
    UNKNOWN = "unknown"          # 操作已执行但读不到明确结果，需人工留意
    TRANSIENT = "transient"      # 上游故障（5xx / 连不上 / 超时），重试后仍失败 -> 不算脚本错
    FAILED = "failed"            # 真失败，需要人工介入

    @property
    def is_error(self) -> bool:
        """是否应让 job 标红。只有 FAILED 算错。"""
        return self is Outcome.FAILED

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
            Outcome.FAILED: "🚨",
        }[self]


#: 上游临时故障对应的 HTTP 状态码
TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})


def classify_status(status_code: int) -> Outcome:
    """按 HTTP 状态码粗分类。200/204 视为 RENEWED，5xx/429 视为 TRANSIENT。"""
    if status_code in (200, 201, 204):
        return Outcome.RENEWED
    if status_code in TRANSIENT_STATUS or status_code == 0:
        return Outcome.TRANSIENT
    return Outcome.FAILED

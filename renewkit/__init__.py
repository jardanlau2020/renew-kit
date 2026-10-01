"""renew-kit：免费主机自动续期脚本的公共骨架。

把各续期仓库重复实现的这几块收成一处：
    · env        环境变量读取与校验、DRY_RUN
    · http       带退避重试的 Session，区分「上游故障」与「真失败」
    · outcome    统一的结果分类（成功/跳过/已满/结果未确认/上游故障/真失败）
    · timeutil   面板到期时间的各种格式解析
    · notify     Telegram 通知（失败不影响 job 结论）
    · report     统一的中文报告排版与退出码（可传 renderer 保留自家排版）

设计原则：
    1. 上游 5xx / 超时 -> TRANSIENT，exit 0，不标红。
    2. 只有确定性的业务失败才是 FAILED，exit 1。
    3. 通知失败不影响续期结论。
"""
from .outcome import Outcome, classify_status
from .report import RenewReport, TargetResult, shorten

__version__ = "0.3.0"

__all__ = [
    "Outcome",
    "classify_status",
    "RenewReport",
    "TargetResult",
    "shorten",
    "__version__",
]

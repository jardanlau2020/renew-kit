"""统一的续期报告渲染与收尾。

每个仓库原先各写一份中文排版逻辑，这里收成一处：
    · 每台服务器两行：一行结论 + 一行细节
    · 只有 FAILED 会让 job 标红
    · 收尾时发 TG 并返回进程退出码
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import notify
from .outcome import Outcome
from .timeutil import days_left, format_expiry, is_day_count


def _expiry_phrase(expire) -> str:
    """把到期信息说成人话：天数是「剩 N 天」，时间点是「MM-DD HH:MM 到期」。"""
    if expire in (None, "", 0):
        return ""
    if is_day_count(expire):
        return f"剩 {int(float(expire))} 天"
    exp = format_expiry(expire)
    return f"{exp} 到期" if exp else ""


@dataclass
class TargetResult:
    """单台服务器 / 单个目标的续期结果。"""

    name: str
    outcome: Outcome
    expire: Any = None
    detail: str = ""

    def _remaining(self) -> str:
        d = days_left(self.expire)
        return f"（剩 {d} 天）" if d is not None else ""

    def lines(self) -> list[str]:
        exp = format_expiry(self.expire)
        rem = self._remaining()
        o = self.outcome

        if o is Outcome.RENEWED:
            l1 = f"{o.icon} {self.name} · 成功续期" + (f"至 {exp}" if exp else "")
            parts = [f"剩余 {days_left(self.expire)} 天"] if days_left(self.expire) is not None else []
            parts.append("服务已自动展期")
            l2 = "ℹ️ " + " · ".join(parts)
        elif o is Outcome.ALREADY_MAX:
            l1 = f"{o.icon} {self.name} · 已达上限{rem}"
            l2 = "ℹ️ 无需再续，下次窗口再检查"
        elif o is Outcome.TRANSIENT:
            l1 = f"{o.icon} {self.name} · 上游暂不可用{rem}"
            l2 = f"ℹ️ {self.detail or '面板故障/超时'} · 本次跳过，等下次排程"
        elif o is Outcome.FAILED:
            l1 = f"{o.icon} {self.name} · 续期未完成{rem}"
            l2 = f"⚠️ {self.detail or '执行失败'} · 请登录面板手动处理"
        else:  # SKIPPED
            l1 = f"{o.icon} {self.name} · 状态良好{rem}"
            info = []
            phrase = _expiry_phrase(self.expire)
            if phrase:
                info.append(phrase)
            info.append(self.detail or "未到续期窗口")
            l2 = "ℹ️ " + " · ".join(info)
        return [l1, l2]


@dataclass
class RenewReport:
    """一次运行的完整报告。"""

    service: str
    results: list[TargetResult] = field(default_factory=list)

    def add(self, name: str, outcome: Outcome, *, expire: Any = None, detail: str = "") -> TargetResult:
        r = TargetResult(name=name, outcome=outcome, expire=expire, detail=detail)
        self.results.append(r)
        return r

    def add_result(self, result: TargetResult) -> TargetResult:
        self.results.append(result)
        return result

    @property
    def worst(self) -> Outcome:
        order = [Outcome.FAILED, Outcome.TRANSIENT, Outcome.RENEWED, Outcome.ALREADY_MAX, Outcome.SKIPPED]
        for o in order:
            if any(r.outcome is o for r in self.results):
                return o
        return Outcome.SKIPPED

    @property
    def exit_code(self) -> int:
        return 1 if any(r.outcome.is_error for r in self.results) else 0

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.outcome.value] = out.get(r.outcome.value, 0) + 1
        return out

    def render(self) -> str:
        if not self.results:
            return f"🟢 {self.service} · 检查完成（未发现服务器实例）"
        blocks = ["\n".join(r.lines()) for r in self.results]
        head = f"【{self.service}】"
        tail = ""
        c = self.counts()
        if c.get("failed"):
            tail = f"\n\n⚠️ 有 {c['failed']} 台需要人工处理"
        return head + "\n" + "\n\n".join(blocks) + tail

    def finish(self, *, notify_tg: bool = True, print_report: bool = True) -> int:
        """渲染 + 打印 + 发 TG + 返回退出码。"""
        text = self.render()
        if print_report:
            print(text, flush=True)
        if notify_tg:
            notify.send(text)
        return self.exit_code

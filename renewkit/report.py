"""统一的续期报告渲染与收尾。

每个仓库原先各写一份中文排版逻辑，这里收成一处：
    · 每台服务器两行：一行结论 + 一行细节
    · 只有 FAILED 会让 job 标红
    · 收尾时发 TG 并返回进程退出码
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import env, notify
from .outcome import Outcome
from .timeutil import days_left, format_expiry, is_day_count


def shorten(text, limit: int = 60) -> str:
    """压平换行 + 截短，避免通知被撑爆或排版乱。

    入参不假定是 str：``shorten(exc)`` 是最常见的误用（在 except 块里
    直接把异常丢进来），而这里要是再抛 AttributeError，就会在异常处理路径上
    二次崩掉 —— 连报告和通知都发不出去。统一 str() 兜住。
    """
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


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
        elif o is Outcome.UNKNOWN:
            l1 = f"{o.icon} {self.name} · 结果未确认{rem}"
            l2 = f"ℹ️ {self.detail or '操作已执行但读不到明确提示'} · 请留意下次运行"
        elif o is Outcome.FAILED:
            l1 = f"{o.icon} {self.name} · 续期未完成{rem}"
            l2 = f"⚠️ {self.detail or '执行失败'} · 请登录面板手动处理"
        else:  # SKIPPED
            l1 = f"{o.icon} {self.name} · 状态良好{rem}"
            info = []
            phrase = _expiry_phrase(self.expire)
            # l1 已经带了「（剩 N 天）」时 l2 再说一遍就是复读机：
            #   🟢 srv · 状态良好（剩 12 天）
            #   ℹ️ 剩 12 天 · 未到续期窗口      <- 这句纯噪音
            # 只有 l1 没写天数（时间点型 expire）时才在 l2 补日期。
            if phrase and phrase not in l1:
                info.append(phrase)
            info.append(self.detail or "未到续期窗口")
            l2 = "ℹ️ " + " · ".join(info)
        return [l1, l2]


@dataclass
class RenewReport:
    """一次运行的完整报告。

    renderer：可选的自定义排版函数。传了就完全接管 render()，
    用来兼容那些已经手工调好通知格式的仓库——它们只想借用 renewkit 的
    结果语义与退出码，排版仍归自己。签名：``fn(report) -> str``。
    """

    service: str
    results: list[TargetResult] = field(default_factory=list)
    renderer: Callable[["RenewReport"], str] | None = None

    def add(self, name: str, outcome: Outcome, *, expire: Any = None, detail: str = "") -> TargetResult:
        r = TargetResult(name=name, outcome=outcome, expire=expire, detail=detail)
        self.results.append(r)
        return r

    def add_result(self, result: TargetResult) -> TargetResult:
        self.results.append(result)
        return result

    @property
    def worst(self) -> Outcome:
        order = [Outcome.FAILED, Outcome.TRANSIENT, Outcome.UNKNOWN,
                 Outcome.RENEWED, Outcome.ALREADY_MAX, Outcome.SKIPPED]
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
        if self.renderer is not None:
            return self.renderer(self)
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
        # 演练时 notify.send() 会把「本轮本应发送」的原文整条打出来 —— 那就是
        # 这份报告本身。这里再打一遍等于同一段字出现两次，所以让位给它。
        # 注意 notify_tg=False 时 notify 根本没被调用，日志里那份得留着。
        if print_report and not (notify_tg and env.dry_run()):
            print(text, flush=True)
        if notify_tg:
            notify.send(text)
        return self.exit_code

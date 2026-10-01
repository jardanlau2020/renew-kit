"""面板到期时间解析。

面板返回的 renewal 字段格式五花八门：
    · 剩余天数        30
    · 秒级时间戳      1790000000
    · 毫秒级时间戳    1790000000000
    · ISO / 日期串    2026-10-31T12:00:00 / 2026-10-31

统一在这里处理，避免每个脚本各写一份。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))

_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ]?(\d{2})?:?(\d{2})?")
_DAYS_AS_NUMBER_MAX = 1_000_000   # 小于这个数当作「剩余天数」
_TIMESTAMP_S_MIN = 1e9
_TIMESTAMP_MS_MIN = 1e11


def is_day_count(value) -> bool:
    """判断 renewal 是「剩余天数」而不是时间点。"""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return False
    return 0 <= n < _DAYS_AS_NUMBER_MAX


def _to_epoch(value) -> float | None:
    """把各种格式归一成 UTC epoch 秒；无法判断返回 None。"""
    if value in (None, "", 0):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        m = _ISO_RE.match(str(value))
        if not m:
            return None
        parts = [int(x) if x else 0 for x in m.groups()]
        try:
            return datetime(*parts, tzinfo=timezone.utc).timestamp()
        except (TypeError, ValueError):
            return None
    if n > _TIMESTAMP_MS_MIN:
        return n / 1000.0
    if n >= _TIMESTAMP_S_MIN:
        return n
    return None


def days_left(value, *, now: float | None = None) -> int | None:
    """返回剩余天数（int）；无法判断返回 None（保守：不触发补点）。"""
    if value in (None, "", 0):
        return None
    now = time.time() if now is None else now
    try:
        n = float(value)
    except (TypeError, ValueError):
        epoch = _to_epoch(value)
        if epoch is None:
            return None
        return int((epoch - now) // 86400)
    if n < _DAYS_AS_NUMBER_MAX:      # 面板直接给「剩余几天」
        return int(n)
    epoch = _to_epoch(n)
    if epoch is None:
        return None
    return int((epoch - now) // 86400)


def format_expiry(value, *, tz: timezone = BEIJING) -> str:
    """格式化成 MM-DD HH:MM 或 MM-DD；无法解析时原样返回前 19 字符。"""
    if value in (None, "", 0):
        return ""
    try:
        n = float(value)
    except (TypeError, ValueError):
        m = _ISO_RE.match(str(value))
        if m:
            g = m.groups()
            return f"{g[1]}-{g[2]}" + (f" {g[3]}:{g[4]}" if g[3] and g[4] else "")
        return str(value)[:19]
    if n < _DAYS_AS_NUMBER_MAX:      # 是「剩余天数」，不是时间点
        return f"{int(n)} 天后"
    epoch = _to_epoch(n)
    if epoch is None:
        return str(value)[:19]
    return time.strftime("%m-%d %H:%M", time.gmtime(epoch + tz.utcoffset(None).total_seconds()))


def now_local(tz: timezone = BEIJING) -> str:
    """本地时间 MM-DD HH:MM（runner 通常是 UTC）。"""
    return time.strftime("%m-%d %H:%M", time.gmtime(time.time() + tz.utcoffset(None).total_seconds()))

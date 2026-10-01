"""环境变量读取与校验。

统一 DRY_RUN 语义、必填校验、多值分隔，避免各脚本重复写 os.environ.get(...)。
"""
from __future__ import annotations

import os
from typing import Sequence

_TRUTHY = frozenset({"1", "true", "yes", "y", "on"})


class MissingEnv(RuntimeError):
    """必填环境变量缺失。"""

    def __init__(self, names: Sequence[str]):
        self.names = list(names)
        super().__init__("缺少必需的环境变量: " + ", ".join(self.names))


def get(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def require(*names: str) -> list[str]:
    """读取一批必填变量，任一缺失则抛 :class:`MissingEnv`。"""
    values = [get(n) for n in names]
    missing = [n for n, v in zip(names, values) if not v]
    if missing:
        raise MissingEnv(missing)
    return values


def get_list(name: str, *, sep: str = ",", default: str = "") -> list[str]:
    """读取逗号分隔的多值变量，自动去空白、去空项。"""
    raw = get(name, default)
    return [x.strip() for x in raw.split(sep) if x.strip()]


def get_int(name: str, default: int) -> int:
    raw = get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"   ⚠️ {name}={raw!r} 不是整数，改用默认值 {default}", flush=True)
        return default


def dry_run(name: str = "DRY_RUN") -> bool:
    return get(name).lower() in _TRUTHY


def is_set(*names: str) -> bool:
    return all(get(n) for n in names)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""没有 pytest 时的降级测试跑法（Windows / pip 不可用环境）。

CI 里正常跑 `pytest -q`（见 .github/workflows/ci.yml）。这个脚本是本地兜底：
用一个最小 pytest 替身（`raises` / `fixture` / `monkeypatch`）把 tests/ 下的
test_* 跑一遍 —— 只覆盖本项目实际用到的子集，不是通用 pytest 替代品。

用法：
    python .verify/run_tests_offline.py
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import traceback
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                       # renew-kit 仓库根
TESTS = ROOT / "tests"


# ------------------------------------------------------- requests 最小替身
def install_requests_stub() -> bool:
    """本机没有 requests 时补一个，好让 renewkit.http 能被 import。"""
    try:
        import requests  # noqa: F401
        return False
    except ImportError:
        pass

    m = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class ConnectionError(RequestException):      # noqa: A001  (对齐 requests 命名)
        pass

    class Timeout(RequestException):
        pass

    class Session:
        def __init__(self, *a, **k):
            self.headers = {}

        def mount(self, *a, **k):
            pass

        def request(self, *a, **k):
            raise NotImplementedError("替身不发真请求")

    m.RequestException = RequestException
    m.ConnectionError = ConnectionError
    m.Timeout = Timeout
    m.Session = Session
    m.Response = type("Response", (), {})
    sys.modules["requests"] = m

    adapters = types.ModuleType("requests.adapters")

    class HTTPAdapter:
        def __init__(self, *a, **k):
            pass

    adapters.HTTPAdapter = HTTPAdapter
    m.adapters = adapters
    sys.modules["requests.adapters"] = adapters

    u3 = types.ModuleType("urllib3")
    sys.modules["urllib3"] = u3
    u3util = types.ModuleType("urllib3.util")
    sys.modules["urllib3.util"] = u3util
    u3retry = types.ModuleType("urllib3.util.retry")

    class Retry:
        def __init__(self, *a, **k):
            pass

    u3retry.Retry = Retry
    sys.modules["urllib3.util.retry"] = u3retry
    return True


# --------------------------------------------------------- pytest 最小替身
def install_pytest_stub() -> bool:
    try:
        import pytest  # noqa: F401
        return False
    except ImportError:
        pass

    m = types.ModuleType("pytest")

    class Raises:
        def __init__(self, exc):
            self.exc = exc
            self.value = None

        def __enter__(self):
            return self

        def __exit__(self, et, ev, tb):
            if et is None:
                raise AssertionError(f"DID NOT RAISE {self.exc}")
            if issubclass(et, self.exc):
                self.value = ev
                return True
            return False

    def fixture(fn=None, **_kw):
        def deco(f):
            f._is_fixture = True
            return f
        return deco(fn) if fn is not None else deco

    m.raises = Raises
    m.fixture = fixture
    sys.modules["pytest"] = m
    return True


class MonkeyPatch:
    """只实现测试实际用到的 setenv / delenv / setattr。"""

    def __init__(self):
        self._undo: list = []

    def setenv(self, name, value):
        old = os.environ.get(name)
        os.environ[name] = str(value)
        self._undo.append(
            lambda: os.environ.pop(name, None) if old is None
            else os.environ.__setitem__(name, old))

    def delenv(self, name, raising=True):
        if name not in os.environ:
            if raising:
                raise KeyError(name)
            return
        old = os.environ.pop(name)
        self._undo.append(lambda: os.environ.__setitem__(name, old))

    def setattr(self, target, name, value):
        old = getattr(target, name)
        setattr(target, name, value)
        self._undo.append(lambda: setattr(target, name, old))

    def undo(self):
        for fn in reversed(self._undo):
            fn()
        self._undo.clear()


# ------------------------------------------------------------------ runner
def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def resolve(mod, name: str, patch: MonkeyPatch):
    if name == "monkeypatch":
        return patch
    fn = getattr(mod, name, None)
    if fn is None or not getattr(fn, "_is_fixture", False):
        raise RuntimeError(f"无法解析 fixture: {name}")
    return fn(**{p: resolve(mod, p, patch) for p in inspect.signature(fn).parameters})


def main() -> int:
    req_stub = install_requests_stub()
    pytest_stub = install_pytest_stub()
    print(f"renew-kit 离线测试 | requests 替身={req_stub} pytest 替身={pytest_stub}")
    print(f"测试目录: {TESTS}\n")

    sys.path.insert(0, str(ROOT))

    passed = 0
    failures: list[tuple[str, str, BaseException, str]] = []

    for path in sorted(TESTS.glob("test_*.py")):
        mod = load_module(path, path.stem)
        for name, fn in sorted(vars(mod).items()):
            if not name.startswith("test_") or not callable(fn):
                continue
            patch = MonkeyPatch()
            try:
                kwargs = {p: resolve(mod, p, patch)
                          for p in inspect.signature(fn).parameters}
                fn(**kwargs)
                passed += 1
                print(f"  \u2705 {path.stem}::{name}")
            except BaseException as exc:                 # noqa: BLE001
                failures.append((path.stem, name, exc, traceback.format_exc()))
                print(f"  \u274c {path.stem}::{name} — {type(exc).__name__}: {exc}")
            finally:
                patch.undo()

    print("\n" + "=" * 62)
    if failures:
        print(f"\u274c {len(failures)}/{passed + len(failures)} 失败\n")
        for stem, name, _exc, tb in failures:
            print(f"--- {stem}::{name} ---")
            print(tb)
        return 1
    print(f"\u2705 全部通过（{passed} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

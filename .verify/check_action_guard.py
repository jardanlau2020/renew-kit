#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 tests/test_action_yml.py 的守卫真的能抓到「空 then 分支」那个 bug。

做法：把 action.yml 的修复点换回坏形状，写进临时文件，然后把守卫模块的
ACTION 指向它，确认两个测试都失败；再把 ACTION 指回真文件，确认都通过。
全程不改动仓库里的 action.yml。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent      # renew-kit 仓库根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_action_yml as guard  # noqa: E402

REAL = guard.ACTION
text = REAL.read_text(encoding="utf-8")

FIXED = '''      env:
        RENEW_SCRIPT: ${{ inputs.script }}
        RENEW_COMMAND: ${{ inputs.command }}
      run: |
        set -uo pipefail'''
BROKEN = '''      run: |
        set -uo pipefail'''

if FIXED not in text:
    print("!! 没在 action.yml 里找到修复后的形状，脚本需要同步更新")
    sys.exit(2)

# 把「Run renewal script」那一步整体换回坏形状
start = text.index(FIXED)
end = text.index('        fi\n', start) + len('        fi\n')
broken_text = text[:start] + '''      run: |
        set -uo pipefail
        if [ -n "${{ inputs.command }}" ]; then
          ${{ inputs.command }}
        else
          python "${{ inputs.script }}"
        fi
''' + text[end:]

tmp = Path(tempfile.mkdtemp()) / "action.yml"
tmp.write_text(broken_text, encoding="utf-8")

results = []


def run_guard(label, action_path):
    guard.ACTION = action_path
    for fn in (guard.test_action_run_blocks_are_valid_shell,
               guard.test_action_has_no_empty_then_branch_shape):
        try:
            fn()
            results.append((label, fn.__name__, "PASS"))
        except AssertionError as exc:
            results.append((label, fn.__name__, f"FAIL: {str(exc).splitlines()[0][:90]}"))


run_guard("坏形状", tmp)
run_guard("真文件", REAL)

print(f"{'场景':8s} {'测试':52s} 结果")
for label, name, res in results:
    print(f"{label:8s} {name:52s} {res}")

ok = all(
    (res == "PASS") == (label == "真文件")
    for label, _name, res in results
)
print()
print("✅ 守卫有效：坏形状被抓到，真文件通过" if ok
      else "❌ 守卫无效：坏形状没被抓到（或真文件误报）")
sys.exit(0 if ok else 1)

"""composite action 的 shell 语法守卫。

回归背景：action.yml 的 "Run renewal script" 曾写成

    if [ -n "${{ inputs.command }}" ]; then
      ${{ inputs.command }}
    else
      python "${{ inputs.script }}"
    fi

`command` 的默认值是空串，于是 then 分支被掏空。POSIX 要求 then 之后至少要有
一条命令，bash 会直接报 ``syntax error near unexpected token 'else'`` 并以
exit 2 收场 —— 后果是「凡是不传 command 的调用方，续期步骤必挂」，而且报错
发生在脚本启动之前，看不出跟续期有任何关系。h0stship 因此每周日红一次。

这个测试把每个 run 块按「占位符取空值」和「取非空值」两种极端各渲染一遍，
交给 ``bash -n`` 检查语法，把这类问题挡在合并前。

本机 bash 不在 PATH（或被别的程序占了）时，用 ``RENEWKIT_BASH`` 指定绝对路径。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ACTION = (Path(__file__).resolve().parents[1]
          / ".github" / "actions" / "renew" / "action.yml")

PLACEHOLDER = re.compile(r"\$\{\{[^}]*\}\}")
_RUN_RE = re.compile(r"^(\s*)run:\s*\|-?\s*$")


def extract_run_blocks(text: str) -> list[str]:
    """抠出 YAML 里所有 ``run: |`` 块。

    只认块标量（不引 pyyaml），够本项目用；提取条数会在测试里兜底断言，
    万一以后格式变了也不会静默漏测。
    """
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        m = _RUN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        i += 1
        body: list[str] = []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                body.append("")
                i += 1
                continue
            if len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line[indent:])
            i += 1
        blocks.append("\n".join(body))
    return blocks


def _resolve_bash() -> str | None:
    override = os.environ.get("RENEWKIT_BASH")
    if override:
        return override
    # 用绝对路径：Windows 上裸名 "bash" 可能被解析到 WSL 的 bash.exe 上，
    # 那不是我们要的 shell。
    return shutil.which("bash")


def _bash_syntax_ok(bash: str, script: str) -> tuple[bool, str]:
    """从 stdin 喂脚本给 ``bash -n``，免去临时文件与路径转换的麻烦。"""
    proc = subprocess.run([bash, "-n"], input=script,
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0, (proc.stderr or "").strip()


def _code_lines(text: str) -> str:
    """去掉 shell 注释行，避免注释里的示例文字被当成代码。"""
    return "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))


def test_action_run_blocks_are_valid_shell():
    assert ACTION.is_file(), f"找不到 {ACTION}"
    blocks = extract_run_blocks(ACTION.read_text(encoding="utf-8"))
    assert len(blocks) >= 5, f"只抠到 {len(blocks)} 个 run 块，提取逻辑可能坏了"

    bash = _resolve_bash()
    if bash is None:
        print("本机没有 bash，跳过语法检查（可用 RENEWKIT_BASH 指定）")
        return

    probe_ok, probe_err = _bash_syntax_ok(bash, "echo ok\n")
    if not probe_ok:
        # 连一句合法脚本都解析不了 → 是环境里的 bash 不对，不是 action 的问题。
        # 说清楚而不是报一堆假失败。
        print(f"bash 不可用（{bash}），跳过语法检查：{probe_err}")
        return

    problems: list[str] = []
    for idx, block in enumerate(blocks, 1):
        for label, value in (("空值", ""), ("非空", "X")):
            ok, err = _bash_syntax_ok(bash, PLACEHOLDER.sub(value, block))
            if not ok:
                problems.append(f"run 块 #{idx}（占位符取{label}）: {err}")
    assert not problems, "\n".join(problems)


def test_action_has_no_empty_then_branch_shape():
    """直接盯住那个坏形状：then 后面紧跟 else / fi，中间没有任何命令。

    占位符被替换成空串后会留下一行纯空白，所以先把空白行挤掉再匹配 ——
    否则 `then` 和 `else` 之间隔着那一行，正则就漏了。
    """
    text = ACTION.read_text(encoding="utf-8")
    rendered = _code_lines(PLACEHOLDER.sub("", text))
    squeezed = "\n".join(l for l in rendered.splitlines() if l.strip())
    bad = re.findall(r"then[ \t]*\n[ \t]*(?:else|fi)\b", squeezed)
    assert not bad, f"发现空的 then 分支：{bad}"

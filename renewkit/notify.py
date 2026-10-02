"""Telegram 通知。

设计原则：通知失败只算通知失败，绝不让 job 标红、也绝不吞掉续期结果。

支持三种发送形态：
    send("纯文本")
    send("<b>富文本</b>", parse_mode="HTML")
    send("点下面去续期", parse_mode="HTML",
         buttons=[{"text": "🔓 去續期", "url": "https://..."}])

DRY_RUN=1（renewkit.env 的真值集合）时只打印不发送，见 send()。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import env

MESSAGE_LIMIT = 4000
TRUNCATION_SUFFIX = "\n... [消息已截断]"


def config() -> tuple[str, str]:
    """兼容两种命名：TG_BOT_TOKEN/TG_CHAT_ID 与 TELEGRAM_TOKEN/TELEGRAM_CHAT_ID。"""
    token = env.get("TG_BOT_TOKEN") or env.get("TELEGRAM_TOKEN")
    chat = env.get("TG_CHAT_ID") or env.get("TELEGRAM_CHAT_ID")
    return token, chat


def build_keyboard(buttons) -> list[list[dict]]:
    """把 ``buttons`` 归一成 Telegram 的 ``inline_keyboard``（行 × 列）。

    接受两种写法，靠第一层元素是不是 list 自动判别：

        # 扁平：一行放完
        [{"text": "A", "url": "..."}, {"text": "B", "url": "..."}]
        -> [[A, B]]

        # 分行：显式排两行
        [[{"text": "A", "url": "..."}], [{"text": "B", "url": "..."}]]
        -> [[A], [B]]

    返回空 list 表示「没有可用按钮」。缺 ``text`` 的按钮会被丢掉——Telegram
    对无 text 的按钮一律 400，宁可少一个按钮也不要整条通知发不出去。
    """
    if not buttons:
        return []

    items = list(buttons)
    rows = items if isinstance(items[0], (list, tuple)) else [items]

    clean: list[list[dict]] = []
    for row in rows:
        cells = []
        for btn in (row or []):
            if not isinstance(btn, dict):
                continue
            if not str(btn.get("text", "")).strip():
                print(f"Telegram 按钮缺 text，已丢弃: {str(btn)[:120]}", flush=True)
                continue
            cells.append(dict(btn))
        if cells:
            clean.append(cells)
    return clean


def send(
    text: str,
    *,
    parse_mode: str | None = None,
    buttons=None,
    timeout: float = 20,
) -> bool:
    """发送一条消息。缺配置或失败返回 False，不抛异常。

    parse_mode：None = 纯文本；"HTML" = 可用 <b>/<i>/<code>（内容要自行转义）。
    buttons   ：内联键盘。扁平 list = 一行；嵌套 list = 多行。见 build_keyboard()。

    用 POST 发送而非把参数拼进 URL——带 HTML tag 的长消息走 GET 容易撞
    URL 长度上限，被 Telegram 以 414 拒掉。reply_markup 是一串 JSON，
    同样只能走 body。

    DRY_RUN 演练：闸门设在这里（唯一一处），内容照打印、一个字节都不发出去。
    RenewReport.finish() 也走这个函数，所以演练开关对所有调用方自动生效。
    """
    token, chat_id = config()
    if len(text) > MESSAGE_LIMIT:
        text = text[: MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX

    rows = build_keyboard(buttons)

    # 演练闸门必须放在最前面，而且只放这一处。
    # 各仓库原先各自抄一遍 `if DRY_RUN: print(...) else: send(...)`，抄漏一个
    # 就是「演练把真通知发出去了」——而 kit 自己的 env.dry_run() 在此之前根本
    # 没人调用（死代码），RenewReport.finish() 在 DRY_RUN=1 下照样真发。
    if env.dry_run():
        print("ℹ️ DRY_RUN 演练，跳过 Telegram 通知。本轮本应发送：", flush=True)
        print(text, flush=True)
        if rows:
            print("   内联按钮: " + json.dumps(rows, ensure_ascii=False), flush=True)
        if not token or not chat_id:
            print("   （注：TG_BOT_TOKEN / TG_CHAT_ID 未配置，实跑时也发不出去）", flush=True)
        return False

    if not token or not chat_id:
        print("Telegram 未配置（TG_BOT_TOKEN / TG_CHAT_ID），跳过通知", flush=True)
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    if rows:
        payload["reply_markup"] = json.dumps(
            {"inline_keyboard": rows}, ensure_ascii=False
        )

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
            if resp.status == 200 and body.get("ok"):
                print("Telegram 通知已发送", flush=True)
                return True
            print(f"Telegram 通知失败: HTTP {resp.status} - {str(body)[:200]}", flush=True)
            return False
    except Exception as exc:
        print(f"Telegram 通知异常: {exc}", flush=True)
        return False

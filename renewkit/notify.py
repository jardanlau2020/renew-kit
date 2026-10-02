"""Telegram 通知。

设计原则：通知失败只算通知失败，绝不让 job 标红、也绝不吞掉续期结果。
"""
from __future__ import annotations

import json
import os
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


def send(text: str, *, parse_mode: str | None = None, timeout: float = 20) -> bool:
    """发送一条消息。缺配置或失败返回 False，不抛异常。

    parse_mode：None = 纯文本；"HTML" = 可用 <b>/<i>/<code>（内容要自行转义）。

    用 POST 发送而非把参数拼进 URL——带 HTML tag 的长消息走 GET 容易撞
    URL 长度上限，被 Telegram 以 414 拒掉。
    """
    token, chat_id = config()
    if not token or not chat_id:
        print("Telegram 未配置（TG_BOT_TOKEN / TG_CHAT_ID），跳过通知", flush=True)
        return False
    if len(text) > MESSAGE_LIMIT:
        text = text[: MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

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

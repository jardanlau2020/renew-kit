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


def send(text: str, *, timeout: float = 20) -> bool:
    """发送一条消息。缺配置或失败返回 False，不抛异常。"""
    token, chat_id = config()
    if not token or not chat_id:
        print("Telegram 未配置（TG_BOT_TOKEN / TG_CHAT_ID），跳过通知", flush=True)
        return False
    if len(text) > MESSAGE_LIMIT:
        text = text[: MESSAGE_LIMIT - len(TRUNCATION_SUFFIX)] + TRUNCATION_SUFFIX

    params = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    })
    url = f"https://api.telegram.org/bot{token}/sendMessage?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            if resp.status == 200 and data.get("ok"):
                print("Telegram 通知已发送", flush=True)
                return True
            print(f"Telegram 通知失败: HTTP {resp.status} - {str(data)[:200]}", flush=True)
            return False
    except Exception as exc:
        print(f"Telegram 通知异常: {exc}", flush=True)
        return False

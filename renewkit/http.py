"""带退避重试的 HTTP 会话。

只对「连接失败 / 超时 / 5xx / 429」重试；4xx（除 429）立即返回，
因为那是确定性错误，重试没意义。
"""
from __future__ import annotations

import html
import re
import time
from typing import Iterable, Sequence

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .outcome import TRANSIENT_STATUS, Outcome

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

#: 默认退避间隔（秒），长度即重试次数
DEFAULT_BACKOFF: tuple[int, ...] = (5, 15, 30)


class TransientError(RuntimeError):
    """上游临时故障，重试耗尽后抛出。调用方应捕获并标记 TRANSIENT。"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def build_session(
    *,
    user_agent: str = DEFAULT_UA,
    accept: str = "application/json",
    content_type: str | None = "application/json",
    headers: dict[str, str] | None = None,
) -> requests.Session:
    """构造一个带统一默认头的 Session（不含 urllib3 层重试，重试由 request() 负责）。"""
    s = requests.Session()
    h = {"User-Agent": user_agent, "Accept": accept}
    if content_type:
        h["Content-Type"] = content_type
    if headers:
        h.update(headers)
    s.headers.update(h)
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=0)))
    s.mount("http://", HTTPAdapter(max_retries=Retry(total=0)))
    return s


def request(
    session: requests.Session,
    method: str,
    url: str,
    *,
    backoff: Sequence[int] = DEFAULT_BACKOFF,
    timeout: float = 25,
    sleep=time.sleep,
    **kwargs,
) -> requests.Response:
    """发起请求，对临时性故障退避重试。

    重试耗尽仍失败时抛 :class:`TransientError`。
    非临时性状态码（4xx 除 429）直接返回 Response，交给调用方判断。
    """
    waits: Iterable[int] = list(backoff) + [None]  # 最后一次不再等待
    last_code = 0
    last_exc: Exception | None = None

    for wait in waits:
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
            last_code = resp.status_code
            if last_code not in TRANSIENT_STATUS:
                return resp
            last_exc = None
        except requests.RequestException as exc:  # 连接失败 / 超时 / DNS
            last_code = 0
            last_exc = exc

        if wait is None:
            break
        print(f"   (HTTP {last_code or 'ERR'}, retry in {wait}s)", flush=True)
        sleep(wait)

    detail = str(last_exc) if last_exc else f"HTTP {last_code}"
    raise TransientError(f"上游重试耗尽: {detail}", status_code=last_code)


def classify(response: requests.Response) -> Outcome:
    """把 Response 粗分类成 Outcome。"""
    return Outcome.TRANSIENT if response.status_code in TRANSIENT_STATUS else (
        Outcome.RENEWED if response.status_code in (200, 201, 204) else Outcome.FAILED
    )


#: HTTP 失败摘要里正文的截断长度
HTTP_ERROR_DETAIL_LIMIT = 240

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def summarize_http_failure(status_code, response_text) -> str:
    """把一次 HTTP 失败的响应压成一行摘要，适合直接塞进通知。

    面板/网关 5xx 经常返回整页 HTML（Cloudflare 的 "Just a moment..."、
    nginx 的 502 页面）。原样发出去会把通知撑爆、也看不出所以然，
    这里只取 ``<title>``；实在没有就退回纯文本并截断。
    """
    text = str(response_text or "").strip()

    title = _TITLE_RE.search(text)
    if title:
        detail = re.sub(r"\s+", " ", html.unescape(title.group(1))).strip()
    elif text.startswith("<"):
        detail = "HTML 错误页（无 title）"
    else:
        detail = re.sub(r"\s+", " ", text).strip()

    if len(detail) > HTTP_ERROR_DETAIL_LIMIT:
        detail = detail[: HTTP_ERROR_DETAIL_LIMIT - 3].rstrip() + "..."

    summary = f"HTTP {status_code}"
    return f"{summary}: {detail}" if detail else summary

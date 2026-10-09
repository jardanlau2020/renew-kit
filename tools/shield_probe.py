#!/usr/bin/env python3
"""shield_probe.py — 只讀探針：量度「某出口 + 某瀏覽器」能唔能過站方嘅反機械人牆。

設計原則
--------
1. **只讀**：唔登入、唔提交、唔撳任何「確認/Claim/Renouveler」按鈕。
   淨係觀察頁面 render 出嚟嘅 Cloudflare 挑戰 / Turnstile 部件狀態。
2. **可換出口**：`--node` 收一條節點 URI，本機起 sing-box，Playwright 走 SOCKS5。
   唔俾就直連（用本機出口）。
3. **可重複**：同一條 URL × 唔同出口，輸出結構化 JSON，方便橫向比較。

判詞（verdict）
--------------
  TURNSTILE_PASS      Turnstile 自動發咗 token（出口「乾淨」）
  TURNSTILE_PENDING   部件 render 咗但冇 token（可能要互動／仲喺等）
  TURNSTILE_FAILED    頁面報驗證失敗（例：Le test a échoué）
  CF_CHALLENGE        撞到 Cloudflare 攔截頁（Just a moment / Checking your browser）
  BLOCKED             403 / 429 / 連唔通
  NO_WIDGET           頁面正常但冇任何反機械人部件
  ERROR               腳本層出錯

用法
----
  xvfb-run -a python3 scripts/shield_probe.py --url https://nowsecure.nl/ --wait 30
  xvfb-run -a python3 scripts/shield_probe.py --url https://fridaydev.fr/services/ \
      --node "vless://..." --wait 45 --shot /tmp/fd_node.png
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                      # --node 模式先需要（本機有 scripts/nodecheck.py）
    import nodecheck as nc  # noqa: E402
except ImportError:       # 移植到其他 repo 時可以冇，改用 --proxy
    nc = None

CHROME_CANDIDATES = [
    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium", "/usr/bin/chromium-browser",
]

# ── 頁面狀態採集（注入到 browser context）──────────────────────────────────
PROBE_JS = r"""
() => {
  const html = document.documentElement ? document.documentElement.innerHTML : '';
  const inp  = document.querySelector('input[name="cf-turnstile-response"]');
  const iframes = Array.from(document.querySelectorAll('iframe'))
      .map(f => (f.src || '(no-src)').slice(0, 100));
  const text = document.body ? (document.body.innerText || '') : '';
  return {
    title: document.title,
    url: location.href,
    ts_api: typeof window.turnstile,
    ts_token_len: inp ? (inp.value || '').length : -1,
    iframes: iframes,
    cf_markers: /just a moment|checking your browser|enable javascript and cookies|cf_chl/i.test(html),
    cf_chl_opt: typeof window._cf_chl_opt !== 'undefined',
    body: text.slice(0, 600)
  };
}
"""

FAIL_PAT = re.compile(r"échec|echoue|échoué|failed|verification failed|error|erreur", re.I)

# ── 用站方 sitekey 喺站方 domain 直接 render Turnstile（唔使登入、唔使等窗口）──
INJECT_JS = r"""
(sk) => {
  window.__probe = {token: '', err: '', widget: false, api: typeof window.turnstile};
  const d = document.createElement('div');
  d.id = '__probe_widget';
  d.style.cssText = 'position:fixed;left:20px;top:20px;z-index:2147483647;background:#111;padding:8px;';
  document.body.appendChild(d);
  if (!window.turnstile) { window.__probe.err = 'turnstile api 未載入'; return; }
  try {
    window.turnstile.render(d, {
      sitekey: sk,
      callback: (t) => { window.__probe.token = t; },
      'error-callback': () => { window.__probe.err = 'error-callback（站方判定失敗）'; },
      'expired-callback': () => { window.__probe.err = 'expired-callback'; },
    });
    window.__probe.widget = true;
  } catch (e) { window.__probe.err = 'render throw: ' + e.message; }
}
"""

def click_challenge(page, out: dict, rect: dict | None) -> str:
    """撳 Turnstile checkbox。優先用 frame 樹反查（frame_element 點擊，實證可靠），
    退返容器座標點擊。回傳用咗邊種方法。"""
    # ① frame 樹反查（closed shadow DOM 睇唔到，但 browser frame 樹睇得到）
    try:
        for f in page.frames:
            if "challenges.cloudflare.com" not in (f.url or ""):
                continue
            el = f.frame_element()
            box = el.bounding_box()
            if box and box.get("width", 0) > 0:
                el.click(position={"x": 30, "y": box["height"] / 2})
                out["click_method"] = "frame_element"
                out["clicked_at"] = (round(box["x"] + 30), round(box["y"] + box["height"] / 2))
                return "frame_element"
    except Exception as e:  # noqa: BLE001
        out["frame_click_error"] = f"{type(e).__name__}: {e}"

    # ② 退返容器座標
    if rect and rect.get("w"):
        cx, cy = rect["x"] + 30, rect["y"] + rect["h"] // 2
        try:
            page.mouse.move(cx - 40, cy - 12)
            time.sleep(0.3)
            page.mouse.move(cx, cy, steps=8)
            time.sleep(0.4)
            page.mouse.click(cx, cy)
            out["click_method"] = "coords"
            out["clicked_at"] = (cx, cy)
            return "coords"
        except Exception as e:  # noqa: BLE001
            out["click_error"] = f"{type(e).__name__}: {e}"
    return "none"


READ_JS = r"""
() => {
  const p = window.__probe || {};
  const w = document.getElementById('__probe_widget');
  let rect = null;
  if (w) { const r = w.getBoundingClientRect(); rect = {x: Math.round(r.x), y: Math.round(r.y),
           w: Math.round(r.width), h: Math.round(r.height)}; }
  return {
    token_len: (p.token || '').length,
    err: p.err || '',
    widget: !!p.widget,
    api: typeof window.turnstile,
    rect: rect,
    widget_html: w ? w.innerHTML.slice(0, 300) : '',
    iframes: Array.from(document.querySelectorAll('iframe'))
        .map(f => (f.src || '(no-src)').slice(0, 100)),
  };
}
"""


def pick_chrome() -> str | None:
    for p in CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return None


def classify(st: dict, code: int) -> tuple[str, str]:
    """(verdict, 人話解釋)"""
    if code in (403, 429) or code == 0:
        return "BLOCKED", f"HTTP {code}（連唔通或被封）"
    if st.get("ts_token_len", -1) > 0:
        return "TURNSTILE_PASS", f"Turnstile 自動發 token（長度 {st['ts_token_len']}）"
    if st.get("cf_markers"):
        return "CF_CHALLENGE", "撞到 Cloudflare 攔截頁（Just a moment 類）"
    body = st.get("body", "")
    if FAIL_PAT.search(body) and (st.get("ts_api") != "undefined" or st.get("ts_token_len", -1) >= 0):
        return "TURNSTILE_FAILED", "頁面報驗證失敗"
    if st.get("ts_api") != "undefined" or st.get("ts_token_len", -1) >= 0:
        return "TURNSTILE_PENDING", "Turnstile 部件 render 咗，但仲未發 token"
    return "NO_WIDGET", "頁面正常，冇偵測到反機械人部件"


def classify_sk(st: dict, code: int) -> tuple[str, str]:
    """sitekey 注入模式嘅判詞。"""
    if code in (403, 429) or code == 0:
        return "BLOCKED", f"HTTP {code}（連唔通或被封）"
    if st.get("token_len", 0) > 0:
        return "TURNSTILE_PASS", f"✅ 站方 Turnstile 自動發 token（長度 {st['token_len']}）"
    err = st.get("err") or ""
    if "error-callback" in err:
        return "TURNSTILE_FAILED", "❌ 站方 Turnstile 報 error-callback（判定失敗）"
    if err:
        return "TURNSTILE_FAILED", f"❌ {err}"
    if st.get("widget"):
        return "TURNSTILE_PENDING", "🟡 部件 render 咗但冇 token（要互動／仲喺等）"
    if st.get("api") == "undefined":
        return "NO_WIDGET", "⚪ Turnstile API 載入唔到（腳本被擋或未 load）"
    return "NO_WIDGET", "⚪ 冇偵測到部件"


def run(url: str, node: str | None, wait: int, shot: str | None, headed: bool,
        slowmo: int = 0, sitekey: str | None = None, click: bool = False,
        click_after: int = 25, click_retries: int = 3, proxy: str | None = None) -> dict:
    from playwright.sync_api import sync_playwright

    out: dict = {"url": url, "node": None, "verdict": None, "reason": None,
                 "samples": [], "http_status": None, "exit_ip": None,
                 "sitekey": sitekey}

    sb_proc = sb_port = sb_cfg = None
    if proxy:                      # 已經有現成 SOCKS5/HTTP 代理（例如 setup_proxy.sh 起好嘅）
        sb_port = proxy
        out["node"] = f"proxy {proxy}"
    elif node:
        if nc is None:
            out["verdict"] = "ERROR"
            out["reason"] = "--node 需要 nodecheck.py（本機 scripts/）；移植版請改用 --proxy"
            return out
        ob = nc.normalize_pitfalls(nc.parse_uri(node))
        wd = tempfile.mkdtemp(prefix="shield_")
        sb_proc, sb_port, sb_cfg = nc.start_singbox(ob, wd)
        time.sleep(4)
        if sb_proc.poll() is not None:
            out["verdict"] = "ERROR"
            out["reason"] = "sing-box 起唔到：\n" + open(f"{wd}/sb.log").read()[-500:]
            return out
        out["node"] = f"{ob['server']}:{ob['server_port']}"

    try:
        with sync_playwright() as p:
            exe = pick_chrome()
            kw: dict = {"headless": not headed,
                        "args": ["--no-sandbox", "--disable-dev-shm-usage",
                                 "--disable-blink-features=AutomationControlled",
                                 "--window-size=1280,900"]}
            if exe:
                kw["executable_path"] = exe
            if sb_port:
                srv = sb_port if "://" in sb_port else f"socks5://127.0.0.1:{sb_port}"
                kw["proxy"] = {"server": srv}
            if slowmo:
                kw["slow_mo"] = slowmo
            browser = p.chromium.launch(**kw)
            ctx = browser.new_context(
                locale="fr-FR", timezone_id="Europe/Paris",
                viewport={"width": 1280, "height": 900},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"),
            )
            page = ctx.new_page()

            # 出口 IP（順便驗證 proxy 真係生效）
            try:
                t = ctx.request.get("https://www.cloudflare.com/cdn-cgi/trace", timeout=25000)
                info = dict(re.findall(r"^(ip|loc|colo|warp)=(.*)$", t.text(), re.M))
                out["exit_ip"] = f"{info.get('ip')} / {info.get('loc')} / colo={info.get('colo')} warp={info.get('warp')}"
            except Exception as e:  # noqa: BLE001
                out["exit_ip"] = f"trace 失敗: {type(e).__name__}"

            resp = None
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                out["http_status"] = resp.status if resp else None
            except Exception as e:  # noqa: BLE001
                out["verdict"] = "ERROR"
                out["reason"] = f"goto 失敗：{type(e).__name__}: {e}"
                out["exit_ip"] = out["exit_ip"]
                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass
                return out

            deadline = time.time() + wait
            best = None
            passed_at = None
            injected = False
            if sitekey:
                try:
                    page.add_script_tag(
                        url="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit")
                    time.sleep(2)
                    page.evaluate(INJECT_JS, sitekey)
                    injected = True
                except Exception as e:  # noqa: BLE001
                    out["verdict"] = "ERROR"
                    out["reason"] = f"注入 Turnstile 失敗：{type(e).__name__}: {e}"

            clicked = False
            clicks = 0
            last_click_t = -999
            while injected and time.time() < deadline:
                try:
                    st = page.evaluate(READ_JS)
                except Exception as e:  # noqa: BLE001
                    st = {"error": f"{type(e).__name__}: {e}"}
                st["t"] = round(wait - (deadline - time.time()), 1)
                out["samples"].append(st)
                if st.get("token_len", 0) > 0:
                    passed_at = st["t"]
                    best = st
                    break
                best = st
                # 等 5 秒畀佢有機會自動過；仲冇 token 就撳一下 checkbox 位
                need_click = click and st["t"] >= click_after and clicks < click_retries
                if need_click and (not clicked or (st["t"] - last_click_t) >= 12):
                    m = click_challenge(page, out, st.get("rect"))
                    clicks += 1
                    clicked = True
                    last_click_t = st["t"]
                    out["click_count"] = clicks
                    print(f"   🖱️  t={st['t']}s 撳 checkbox #{clicks} via {m} @ {out.get('clicked_at')}")
                    if shot:
                        time.sleep(3)
                        try:
                            page.screenshot(path=shot.replace(".png", f"_after{clicks}.png"))
                        except Exception:  # noqa: BLE001
                            pass
                time.sleep(2)

            while (not injected) and time.time() < deadline:
                try:
                    st = page.evaluate(PROBE_JS)
                except Exception as e:  # noqa: BLE001
                    st = {"error": f"{type(e).__name__}: {e}"}
                st["t"] = round(wait - (deadline - time.time()), 1)
                out["samples"].append(st)
                v, _ = classify(st, out["http_status"] or 200)
                if v == "TURNSTILE_PASS" and passed_at is None:
                    passed_at = st["t"]
                    best = st
                    break
                if best is None or (st.get("cf_markers") is False and v != "NO_WIDGET"):
                    best = st
                time.sleep(2)

            final = best or (out["samples"][-1] if out["samples"] else {})
            code = out["http_status"] or 200
            v, why = classify_sk(final, code) if injected else classify(final, code)
            out["verdict"] = v
            out["reason"] = why
            out["passed_after_s"] = passed_at
            out["final"] = {k: final.get(k) for k in
                            ("title", "url", "ts_api", "ts_token_len", "iframes", "cf_markers",
                             "body", "token_len", "err", "widget", "api", "widget_html", "rect")}

            if shot:
                try:
                    page.screenshot(path=shot, full_page=False)
                    out["shot"] = shot
                except Exception as e:  # noqa: BLE001
                    out["shot"] = f"截圖失敗: {e}"
            browser.close()
    finally:
        if sb_proc:
            sb_proc.terminate()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--node", help="節點 URI（本機起 sing-box；唔俾就直連）")
    ap.add_argument("--proxy", help="現成代理，例如 socks5://127.0.0.1:1080（可移植模式）")
    ap.add_argument("--sitekey", help="站方 Turnstile sitekey；喺 --url 嘅 domain 上直接 render")
    ap.add_argument("--click", action="store_true", help="render 之後撳一下 checkbox（量度過唔過得到）")
    ap.add_argument("--click-after", type=int, default=25, help="幾秒後撳（預設 25，太早撳會即刻判敗）")
    ap.add_argument("--click-retries", type=int, default=3, help="最多撳幾次（預設 3，隔 12 秒）")
    ap.add_argument("--wait", type=int, default=30, help="觀察秒數（預設 30）")
    ap.add_argument("--shot", help="截圖輸出 path")
    ap.add_argument("--headless", action="store_true", help="用無頭（預設有頭，需 xvfb）")
    ap.add_argument("--json", action="store_true", help="只印 JSON")
    a = ap.parse_args()

    if not a.headless and not os.environ.get("DISPLAY"):
        print("⚠️ 冇 DISPLAY 又想有頭——用 `xvfb-run -a python3 ...` 包住", file=sys.stderr)

    r = run(a.url, a.node, a.wait, a.shot, headed=not a.headless, sitekey=a.sitekey, click=a.click, click_after=a.click_after,
        click_retries=a.click_retries, proxy=a.proxy)

    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        icon = {"TURNSTILE_PASS": "✅", "TURNSTILE_PENDING": "🟡", "TURNSTILE_FAILED": "❌",
                "CF_CHALLENGE": "🛡️", "BLOCKED": "⛔", "NO_WIDGET": "⚪", "ERROR": "💥"}.get(r["verdict"], "?")
        print(f"── 探針：{r['url']}" + (f"  [sitekey {r['sitekey']}]" if r.get("sitekey") else ""))
        print(f"   出口：{r['node'] or '直連（本機）'} → {r['exit_ip']}")
        print(f"   HTTP {r['http_status']} | title={ (r.get('final') or {}).get('title') }")
        print(f"   {icon} 判詞：{r['verdict']} —— {r['reason']}")
        if r.get("passed_after_s") is not None:
            print(f"   ⏱️  {r['passed_after_s']} 秒自動過")
        f = r.get("final") or {}
        if r.get("sitekey"):
            rect = f.get("rect") or {}
            print(f"   api={f.get('api')} widget={f.get('widget')} "
                  f"尺寸={rect.get('w')}x{rect.get('h')} token_len={f.get('token_len')} "
                  f"err={f.get('err')!r} iframes={len(f.get('iframes') or [])}")
        else:
            print(f"   turnstile api={f.get('ts_api')} token_len={f.get('ts_token_len')} "
                  f"cf_markers={f.get('cf_markers')} iframes={len(f.get('iframes') or [])}")
        if f.get("body"):
            print(f"   body[:160]={f['body'][:160]!r}")
        if r.get("shot"):
            print(f"   📷 {r['shot']}")
    return 0 if r["verdict"] == "TURNSTILE_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

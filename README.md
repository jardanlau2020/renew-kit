# renew-kit

免费主机自动续期脚本的**公共骨架**。

一批 `*-renew` 仓库各自实现了同一套东西（重试、结果判断、Telegram 通知、中文报告排版），
结果是同一个 bug 要修 N 遍——最典型的就是**面板返回 5xx 时被误判成脚本失败，job 天天标红**。

这个库把那几块收成一处，各仓库只留「登录 + 续期」的业务逻辑。

## 模块

| 模块 | 作用 |
|---|---|
| `renewkit.env` | 环境变量读取、必填校验、`DRY_RUN`、逗号分隔多值 |
| `renewkit.http` | 带退避重试的 `Session`；只重试连接失败 / 超时 / 5xx / 429 |
| `renewkit.outcome` | 统一结果分类：`RENEWED` / `SKIPPED` / `ALREADY_MAX` / `TRANSIENT` / `FAILED` |
| `renewkit.timeutil` | 面板 `renewal` 字段的各种格式解析（天数 / 秒 / 毫秒 / ISO） |
| `renewkit.notify` | Telegram 通知；HTML 富文本 + **内联键盘按钮**；**失败不影响 job 结论** |
| `renewkit.report` | 统一中文报告排版与退出码 |

## 核心原则

1. **上游故障 ≠ 脚本失败。** 5xx / 522 / 超时 → `TRANSIENT` → `exit 0`，不标红。
2. **只有确定性的业务失败才是 `FAILED`**（登录失败、404、接口报错）→ `exit 1`。
3. **通知失败不影响续期结论。**
4. 剩余天数读不到时**保守不补点**，宁可不续也不乱点。

## 用法

### 1. 脚本里

```python
from renewkit import RenewReport, Outcome
from renewkit import env, notify
from renewkit.http import build_session, request, TransientError

PANEL_URL = env.get("PANEL_URL", "https://panel.example.com")
USER, PASS = env.require("PANEL_USER", "PANEL_PASS")
SERVERS = env.get_list("SERVER_IDS")
DRY = env.dry_run()

report = RenewReport("Host-Ship")
session = build_session()

for sid in SERVERS:
    try:
        resp = request(session, "POST", f"{PANEL_URL}/api/client/servers/{sid}/renew")
    except TransientError as exc:          # 上游挂了，不算错
        report.add(sid, Outcome.TRANSIENT, detail=str(exc))
        continue
    if resp.status_code in (200, 204):
        report.add(sid, Outcome.RENEWED, expire=resp.json().get("renewal"))
    elif "cannot add more than 30 days" in resp.text.lower():
        report.add(sid, Outcome.ALREADY_MAX)
    else:
        report.add(sid, Outcome.FAILED, detail=f"HTTP {resp.status_code}")

raise SystemExit(report.finish())          # 渲染 + 发 TG + 返回退出码
```

### 2. workflow 里（composite action）

```yaml
jobs:
  renew:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: jardanlau2020/renew-kit/.github/actions/renew@main
        with:
          script: renew.py
        env:
          # 面板凭据走这里，composite action 会继承，无需在 action 内声明
          PANEL_URL: ${{ secrets.PANEL_URL }}
          PANEL_USER: ${{ secrets.PANEL_USER }}
          PANEL_PASS: ${{ secrets.PANEL_PASS }}
          SERVER_IDS: ${{ secrets.SERVER_IDS }}
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
```

## 通知

```python
from renewkit import notify

notify.send("纯文本")
notify.send("<b>续期成功</b>", parse_mode="HTML")

# 内联键盘：扁平 list = 一行
notify.send(
    "🔓 检测到 VPS 需要人工续期",
    parse_mode="HTML",
    buttons=[{"text": "去續期", "url": "https://panel.example.com/vps/1"}],
)

# 也可以显式分行
notify.send(
    "选择操作",
    buttons=[
        [{"text": "续期", "url": "https://a"}],
        [{"text": "面板", "url": "https://b"}, {"text": "文档", "url": "https://c"}],
    ],
)
```

按钮 dict 直接透传 Telegram 的 schema，`url` / `callback_data` / `web_app` 都能用。
缺 `text` 的按钮会被丢掉——Telegram 对无 text 的按钮一律 400，宁可少一个按钮，
也不要整条通知发不出去。

配置读 `TG_BOT_TOKEN`/`TG_CHAT_ID`（也认 `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID`）。
**没配置就打印一行日志返回 `False`，不抛异常。**

### 演练：`DRY_RUN=1`

闸门在 `notify.send()` 里，**只有这一处**——所以 `RenewReport.finish()` 也自动
跟着生效。演练时内容照打印（含内联按钮的 JSON），一个字节都不发出去：

```
ℹ️ DRY_RUN 演练，跳过 Telegram 通知。本轮本应发送：
🚨 Openworld e2ce269b · 续期未完成（剩 5 天）
⚠️ 需人工過驗證碼 · 请登录面板手动处理
   内联按钮: [[{"text": "🔓 去續期", "url": "https://..."}]]
```

返回值仍是 `False`（确实没发出去），退出码不受影响。演练 + 没配 TG 也能看到预览，
方便在没有 secret 的 CI 上验排版。

> 为什么收进 `notify`：各仓库原先各抄一遍 `if DRY_RUN: print(...) else: send(...)`，
> 抄漏一处就是「演练把真通知发出去了」。而 kit 自己的 `env.dry_run()` 在此之前
> 根本没被调用过（死代码），`finish()` 走 `notify.send()` 直接绕过了所有闸门。

## 报告排版

每台服务器两行：一行结论 + 一行细节。

```
【Openworld VPS】
🟢 Openworld e2ce269b · 状态良好（剩 12 天）
ℹ️ 未到续期窗口

🚨 Openworld e2ce269b · 续期未完成（剩 5 天）
⚠️ 服务器状态：正常（Running） · 需人工過驗證碼 · 请登录面板手动处理
```

天数只出现一次（`（剩 N 天）` 在第一行），第二行不复读。

需要**带内联按钮**的通知时，别用 `finish()`——它没有 `buttons` 参数。
改成自己发：

```python
code = report.finish(notify_tg=False)      # 只打印 + 算退出码
notify.send(text, parse_mode="HTML", buttons=buttons or None)
```

排版想完全自己来（保留历史通知格式），传 `renderer=`：

```python
RenewReport("svc", renderer=lambda r: my_format(r))
```

## 变更

| 版本 | 内容 |
|---|---|
| v0.5.3 | `report`：演练时 `finish()` 不再把报告打两遍（预览就是那份报告） |
| v0.5.2 | `notify`：`DRY_RUN` 演练闸门收口（原先 `env.dry_run()` 是死代码，`finish()` 在演练下照样真发） |
| v0.5.1 | `report`：`SKIPPED` 不再把天数复读两遍 |
| v0.5.0 | `notify`：内联键盘按钮（`buttons=` / `build_keyboard()`） |
| v0.4.2 | `action`：修 composite action 空 `then` 分支导致默认路径必挂 |
| v0.4.1 | `report`：`shorten()` 兜住非字符串入参 |
| v0.4.0 | 回灌 c10udcheckin 的两项通知能力 |
| v0.3.0 | `RenewReport` 支持 `renderer` 自定义排版 |

## 开发

```bash
pip install -e ".[dev]"
pytest -q
```

本机没有第三方包时，用离线跑法（只依赖标准库）：

```bash
python .verify/run_tests_offline.py
```

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
| `renewkit.notify` | Telegram 通知；**失败不影响 job 结论** |
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

## 开发

```bash
pip install -e ".[dev]"
pytest -q
```

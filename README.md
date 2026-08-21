# OpenCode-widget

OpenCode / Codex / Go 用量悬浮窗 —— 透明玻璃面板，实时显示 OpenCode Go 配额、模型用量与费用。

![OpenCode-widget 截图](screenshot.png)

![OpenCode-widget 大屏](screenshot-large.png)

## 特性

- **透明悬浮窗**：Electron 原生透明窗口，背景透出桌面；文字与 SVG 内容保持清晰，透明度可调
- **三档尺寸**：最小屏（吸顶 Dock）/ 中屏 / 大屏仪表盘
- **吸顶自动变小屏**：窗口拖到屏幕顶部自动吸附并切换为最小屏（Mac Dock 风格），拖离自动恢复
- **实时用量**：滚动（5h）/ 每周 / 每月 Go 配额百分比 + 剩余时间
- **供应商 Tab**：全部 / Go / Kilo / Zen / Router 一键切换，联动模型明细、曲线与热力图
- **模型明细**：按 全部/Go/Free 分组、供应商筛选、模型用量排行、历史曲线（真实时间轴）
- **总量曲线**：切到供应商时默认显示该供应商聚合总量曲线，无需逐个点模型
- **Free 模型**：自动汇总所有来源（router/zen/kilo/gateway 及扫描发现的未用模型）的免费用量，不随供应商筛选而消失
- **大屏仪表盘**：GitHub 风格热力图、统计卡片（跟随时间范围）、输入/输出/缓存拆分
- **按来源计价**：同一模型（如 hy3）在不同供应商下的免费/付费状态独立判定，与官方一致
- **官方数据同步**：应用内登录 opencode.ai 自动抓取 Cookie 与 workspace，同步官方配额数值
- **云端公式主动同步**：每 15 分钟自动拉取云端公式规则（计量系数/供应商/视图定义），版本变化即时生效，失败保留上一版
- **官方计量口径**：总用量按官方系数（1.4212）折算，与 opencode.ai 账单一致；明细/曲线保持原始账单
- **快捷键**：`Tab` 切换供应商 · `←/→` 切换供应商 · `↑/↓` 切换模型
- **边缘 tooltip**：历史曲线悬停详情在图表右缘自动翻转到左侧，不被裁剪

## 架构

```
┌─────────────────────┐      HTTP/JSON       ┌──────────────────────────────┐
│  data_server.py     │ ───────────────────► │  Electron 透明窗口            │
│  · 读本地数据        │  127.0.0.1:8765      │  · 渲染进程直连数据服务        │
│  · 计算用量/统计      │                      │  · 窗口交互/吸顶/透明度         │
│  · 启动预热+缓存      │                      │  · 供应商/模型/曲线联动         │
│  · 官方数据同步      │                      └──────────────────────────────┘
└─────────────────────┘
```

- **Python 只负责取数与计算**（`data_server.py` + `go-usage-widget.py` 数据函数 + `server_data.py` 官方同步 + `views.py` 云端公式引擎）
- **前端与窗口交互交给 Electron**（`electron/main.js` / `preload.js` / `app/index.html`）
- **无痕启动**：直接拉起 GUI 进程（`pythonw.exe` + `electron.exe`），不经过 cmd/npm，双击不闪黑框

## 快速开始

### 无痕启动（推荐）

双击 `启动Go用量悬浮窗.vbs` —— 无任何控制台窗口，且自带防重复（已在运行时不会重复拉起）：

```bat
启动Go用量悬浮窗.vbs
```

> 说明：`.vbs` 经 `wscript.exe` 运行，默认不显示控制台窗口，是唯一真正"零黑框"的双击入口。
> `.cmd` 双击时 Windows 会拉起 cmd.exe 承载脚本，必然闪一下黑框，仅作兼容入口保留。

### 手动启动

```bat
start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" data_server.py
start "" electron\node_modules\electron\dist\electron.exe electron
```

### 开机自启（可选）

把 `启动Go用量悬浮窗.vbs` 放入启动文件夹即可（Win+R 输入 `shell:startup`），或建一个指向它的桌面快捷方式。

### 依赖

- Python 3.11（内置 http.server，无第三方后端依赖）
- Node.js + Electron（`electron/` 内执行 `npm install` 安装依赖）

## 使用说明

### 窗口形态

| 形态 | 触发 | 用途 |
|------|------|------|
| 最小屏 | 拖到屏幕顶部吸附 / 点最小化 | 吸顶 Dock，只看滚动配额 |
| 中屏 | 默认 | 配额 + 模型明细列表 |
| 大屏 | 点右上角展开 | 供应商 Tab + 热力图 + 统计卡 + 曲线 + 明细 |

### 供应商切换

顶部 Tab 或快捷键 `←/→`（大屏）/ `Tab`（任意屏）。切供应商后：
- 自动回到"全部"分类并显示该供应商的**聚合总量曲线**（`__total__`，不出现在模型明细列表）
- 模型明细、统计卡、热力图同步跟随该供应商

### 模型明细

- 上方面板按 **全部 / Go / Free** 分组过滤（Free 聚合所有来源的免费模型）
- 点击某模型 → 选中，右侧曲线/热力图切到该模型（Go/全部 模式会自动把顶部供应商同步过去；Free 模式不切供应商）
- 再次点击已选模型 → 取消选中，回到供应商总量曲线
- 快捷键 `↑/↓` 在模型间循环；在第一个模型上再按 `↑` 回到总量曲线

### 曲线悬停

鼠标滑过历史曲线显示当天 Token / 次数 / 费用详情；在图表右缘会自动翻转到鼠标左侧，避免被容器裁剪。

### 小屏时间范围（快捷键 X）

小屏顶部的"总用量"默认显示**今天**的数据。按 `X` 键循环切换：**今天 → 近7天 → 全部 → 今天**。主金额与底部统计格（总Token/总费用/总次数/活跃天数）随范围联动，选择会记住（localStorage）。

> 说明：小屏统计基于历史逐日数据聚合，跟随当前供应商（或全部）；"全部"即全部历史。

### Go 配额重置倒计时

选中 Go 供应商时，小屏会在模型指标与总Token之间显示一行橙色小字：`⏳ 重置: 5 hours 0 minutes后`——对应 Go 平台官方滚动配额窗口（Session/周/月按当前时间范围取对应窗口：今天→Session、近7天→周、全部→月）的剩余重置时间。

### 登录同步官方配额

右键菜单 → **用浏览器登录**（打开 opencode.ai）或 **自动抓取 Cookie 同步数值**（应用内登录窗口，自动捕获 auth cookie + workspace_id）。登录态持久化，下次免登录。

### 右键菜单

立即刷新 / 用浏览器登录 / 自动抓取 Cookie 同步数值 / 服务器配置（手动）/ 手动校准 / API Key 设置

## 数据来源

- 本地 `~/.local/share/opencode/opencode.db`（OpenCode 会话用量）+ `~/.codex/logs_2.sqlite`
- 官方 opencode.ai Go 配额（配置 auth cookie 后经 `POST /api/sync` 同步）
- 本地 `server_usage.db` 是官方数据的账本镜像（自动同步，与仓库数据独立）
- 云端公式（计量系数 / 供应商 / 视图定义）从 Cloudflare Worker URL 拉取，每 15 分钟自动同步；本地 `views.py` 提供默认公式兜底

## 计量口径说明

- **总用量 / 小屏主金额** 显示官方口径：账单成本 × 官方计量系数（当前 1.4212，由云端公式 `params.meter.ratio` 定义，云端可变），与 opencode.ai 账单（封顶配额）一致
- **模型明细 / 每日曲线 / tooltip** 保持原始账单成本，避免明细虚高
 - 切换时间范围（今天/近7天/全部）与供应商时，主金额随之联动，均为官方口径

## 公式与数据源

### 架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Cloudflare Worker (opencode-formula.opencode-widget.workers.dev)      │
│  · cloud/formula.json → build_worker.py → formula-worker.js           │
│  · 提供云端公式：params(动态变量) + views(视图定义)                     │
└───────────────────────────────────────┬─────────────────────────────────┘
                                        │ HTTP/JSON (每 15 分钟同步)
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  data_server.py (本地)                                                  │
│  · FormulaStore 拉取/缓存/回退 (TTL 900s, version 热更新)              │
│  · apply_params_to_gw(): 云端 params → gw 模块级规则表                  │
│  · 失败静默保留上一版，本地 views.py DEFAULT_FORMULA 兜底              │
└───────────────────────────────────────┬─────────────────────────────────┘
                                        │ 127.0.0.1:8765 /api/*
                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  go-usage-widget.py (计算引擎)                                          │
│  · model_stats / supplier_stats / history 等数据函数                    │
│  · 按云端公式计算 est_req_cost / model_quota / model_remain 等字段     │
└─────────────────────────────────────────────────────────────────────────┘
```

### 公式来源与优先级

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | 云端 Worker (`/formula`) | `config.json` 的 `formula_url` 指向的 URL |
| 2 | 本地缓存 | `FormulaStore` 内存缓存，TTL 900s，version 变化时自动热更新 |
| 3 | 本地默认 | `views.py` `DEFAULT_FORMULA`，离线/云端故障时回退 |

`apply_params_to_gw()` 将云端 `params` 覆盖到 `gw` 模块级规则表（`MODEL_QUOTAS`/`PRICES`/`REQ_LIMITS`/`TOKENS_PER_REQ`/`LIMITS` 等），**缺 key 保持本地默认**，保证降级一致。

### params 参数字典

| 参数 | 类型 | 含义 | 来源 | 改动影响 |
|------|------|------|------|---------|
| `meter.ratio` | float | 计量系数（账单×ratio = 官方口径） | 云端 | 总用量/小屏主金额 |
| `limits.monthly` | float | 月度基础限额（USD） | 云端 | 全局封顶 |
| `limits.weekly` | float | 周限额（USD） | 云端 | 周窗口配额 |
| `limits.session` | float | 会话限额（USD） | 云端 | 滚动窗口配额 |
| `limits.credit_per_applied` | float | 每笔 credits 扩容额度（USD） | 云端 | 全局限额 = monthly + applied_credits × credit_per_applied |
| `windows.session_ms` | int | 会话窗口时长（毫秒） | 云端 | 重置倒计时 |
| `windows.week_ms` | int | 周窗口时长（毫秒） | 云端 | 重置倒计时 |
| `sources.paid` | list | 付费供应商列表 | 云端 | meterRatio 作用范围 |
| `sources.subset_of` | dict | 子集供应商归属（如 gateway→go） | 云端 | 模型列表去重/聚合 |
| `free_models.*` | dict | 免费模型判定规则 | 云端 | free 分组 |
| `providers.src` | dict | provider → source 映射 | 云端 | 数据来源分类 |
| `providers.prefixes` | list | provider 前缀列表 | 云端 | 模型来源识别 |
| `prices` | dict | 每模型 in/out/cr/cw 价格（USD/M token） | 云端 | est_req_cost / est_tok_cost |
| `model_quotas` | dict | 每模型月度使用额度（USD） | 云端 | 模型独立剩余 |
| `req_limits` | dict | 每模型月度请求数上限 [low, mid, high] | 云端 | est_req_cost = quota / high |
| `tokens_per_req` | dict | 每模型每次请求平均 token 数 | 云端 | est_tok_cost = est_req_cost / tokens_per_req |
| `display_names` | dict | 模型显示名 | 云端 | 前端模型名称 |
| `refresh.formula_s` | int | 公式同步间隔（秒） | 云端 | 后台同步频率 |

### 计算字段追溯表

> 行 = 前端/API 中每个显示数据字段；列 = 来源 / 计算方式 / 代码位置。
> 公式注册表为单一真相源，见 `formula_registry.py`；云端 formula.json v4 `formulas` 节为下发副本。

| 显示字段 | 来源 | 计算方式 | 代码位置 |
|----------|------|---------|---------|
| **小屏主金额（总用量）** | 官方 `cost_summary`（原始账单 Σcost） | `Σcost × meter.ratio`（scope 为 all 或 source in paid） | `views.py` `ViewEngine._apply_post` |
| **供应商 cost（大窗统计卡）** | 本地聚合（按 src/model/日期） | `Σcost`（原始账单，不乘 ratio） | `data_server.py` `/api/views` |
| **suppliers.go.cost** | `remote_rows`(官方 `cost_map`) + `extra`(本地补充) | 官方优先，本地仅补充官方没有的 | `data_server.py` `_collect_rows` |
| **模型明细 cost_m / cost_w / cost_s** | 本地聚合 `s["cost_m"]` 等 | `Σcost`（原始账单） | `go-usage-widget.py` `model_stats` |
| **est_req_cost（官方估算次均费用）** | 云端 `params.model_quotas` + `req_limits` | `model_quota / req_limits[2]` | `formula_registry.py` → `go-usage-widget.py` |
| **est_tok_cost（官方估算每 token 费用）** | `est_req_cost` + `tokens_per_req` | `est_req_cost / tokens_per_req[m]` | `formula_registry.py` → `go-usage-widget.py` |
| **model_quota（模型月度额度）** | 云端 `params.model_quotas` | 直接读取，缺省 fallback `LIMITS["monthly"]` | `formula_registry.py` → `go-usage-widget.py` |
| **model_used（模型已用费用）** | 官方 `cost_map[m]`（go 来源优先）或本地 `cost_total` | `cost_map[m]`（原始账单，未乘 ratio） | `formula_registry.py` → `go-usage-widget.py` |
| **model_remain（模型剩余额度）** | 计算 | `max(0, model_quota - model_used)` | `formula_registry.py` → `go-usage-widget.py` |
| **effectiveRemain（有效剩余）** | 计算 | `min(model_remain, global_remain)` | `formula_registry.py` → `data_server.py` → `index.html` |
| **remainCnt（剩余次数）** | `effectiveRemain` + `avgPerReq` | `effective_remain / avg_per_req` | `formula_registry.py` → `data_server.py` → `index.html` |
| **avgPerReq（次均费用）** | 实际数据或官方估算 | 有数据时 `c / usedCnt`，否则 `est_req_cost` | `formula_registry.py` → `data_server.py` → `index.html` |
| **token 配额 tq_m / tq_w / tq_s** | `model_quota` + 实际均价 | `cq_monthly / avg_monthly_cost` | `formula_registry.py` → `go-usage-widget.py` |
| **token 使用百分比 tp_m / tp_w / tp_s** | `monthly_tok` + `tq_m` | `min(100, monthly_tok / tq_m × 100)` | `formula_registry.py` → `go-usage-widget.py` |
| **配额百分比（小屏顶部）** | 官方 `quota_snapshot` | `pct = used / limit` | `data_server.py` `/api/state` |
| **全局限额 quotaLimit** | 官方 `limits.monthly` + `applied_credits` | `monthly + applied_credits × credit_per_applied` | `formula_registry.py` → `go-usage-widget.py` |
| **全局已用 usedAll** | 官方 `cost_summary`（付费来源 Σcost） | `Σcost`（原始账单） | `formula_registry.py` → `go-usage-widget.py` |
| **cache_hit（缓存命中率）** | 本地 `usage_records` | `cache_read / (cache_read + tokens_in) × 100` | `formula_registry.py` → `go-usage-widget.py` |
| **rate（速率）** | 本地 `usage_records` | `tok_sec_sum / tok_sec_n` | `formula_registry.py` → `go-usage-widget.py` |
| **token 配额反推(前端)** | `model_quota` + 实际均价 | `used_tokens + remain_cost / avg_cost_per_token` | `formula_registry.py` → `data_server.py` → `index.html` |

### 官方口径 vs 原始账单（v5 分模型折算率）

- **原始账单 cost**：官方 API 返回的 `cost_summary` / `cost_map` 值，是各模型实际消费金额。
- **官方口径 used**：官方内部计价的"已用量"，与原始账单**不是线性关系**——每个模型有独立折算率：

```
官方 used = 0.21 + Σ( 模型消费 × 该模型折算率 )
窗口进度% = ( used − 已用抵扣×$5 ) / 基础额度        ← 月60 / 周30 / 5h=12
```

- **分模型折算率**（2026-08 数据拟合，RMSE=$0.16；云端 `params.meter.rates` 可覆盖）：

| 模型 | 折算率 | 模型 | 折算率 |
|---|---|---|---|
| deepseek-v4-pro | ×3.69 | gpt-5.6-luna | ×0.72 |
| glm-5.2 | ×2.50 | deepseek-v4-flash | ×0.60 |
| kimi-k3 | ×0.94 | 其他模型（默认） | ×0.56 |

- **抵扣规则**：每条 referral credit 抵扣 $5 已用量（从 used 中减），**不扩容分母**——官方 pct 分母恒为基础额度 12/30/60。
- **窗口定义**：月=滚动30天；周=周期首请求锚定（非滚动7天）；5h=5小时周期制。本地以滚动窗口近似，有官网抓取时被官方 pct 覆盖。
- **模型明细/曲线/tooltip**：显示折算后金额（消费×折算率），与总进度可直接对账。

### 更新云端公式

1. 修改 `cloud/formula.json`（params + constants + formulas 三节）
2. 运行 `python cloud/build_worker.py` 生成 `cloud/formula-worker.js`
3. 部署到 Cloudflare Worker（参考 `wrangler deploy` 或上传脚本）
4. 本地 `data_server.py` 每 900s 自动拉取，版本变化时热更新；云端故障时静默保留上一版

> 本地 `views.py` `DEFAULT_FORMULA` 为离线兜底，应与云端版本保持同步（当前均为 v4）。
> 公式注册表 `formula_registry.py` 为本地执行层，云端只下发元数据（display/source/expr/params），不执行任意代码。

### 本地同步机制

- `data_server.py` 启动时调用 `get_formula()` 拉取云端公式
- 后台线程 `formula_sync_loop()` 每 900 秒强制拉取，检测 `version` 变化
- 版本变化时自动 `apply_params_to_gw()` 覆盖本地规则表 + 重建 `ViewEngine`
- 云端/网络故障时静默保留上一版已生效公式，不中断服务

## 目录结构

```
data_server.py            # 数据服务（HTTP/预热/缓存/API）
go-usage-widget.py        # 数据计算函数（用量/统计/历史/热力图/来源计价）
server_data.py            # 官方用量抓取与落库
views.py                  # 云端公式引擎（拉取/缓存/回退 + 视图聚合与计量系数）
browser_cookie.py         # 浏览器 Cookie 获取辅助
启动Go用量悬浮窗.cmd      # 兼容启动入口（会闪一次黑框）
启动Go用量悬浮窗.vbs      # 无痕启动入口（推荐）
electron/
  main.js                 # 透明窗口/吸顶/尺寸/登录抓取
  preload.js              # 最小 IPC 桥
  app/index.html          # 前端界面
DEBUG.md                  # 已知问题与恢复手册
```

## 开发排障

遇到疑难问题先查 `DEBUG.md`（记录了历史 bug 的根因与恢复步骤）。
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

- **Python 只负责取数与计算**（`data_server.py` + `go-usage-widget.py` 数据函数 + `server_data.py` 官方同步）
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

### 登录同步官方配额

右键菜单 → **用浏览器登录**（打开 opencode.ai）或 **自动抓取 Cookie 同步数值**（应用内登录窗口，自动捕获 auth cookie + workspace_id）。登录态持久化，下次免登录。

### 右键菜单

立即刷新 / 用浏览器登录 / 自动抓取 Cookie 同步数值 / 服务器配置（手动）/ 手动校准 / API Key 设置

## 数据来源

- 本地 `~/.local/share/opencode/opencode.db`（OpenCode 会话用量）+ `~/.codex/logs_2.sqlite`
- 官方 opencode.ai Go 配额（配置 auth cookie 后经 `POST /api/sync` 同步）
- 本地 `server_usage.db` 是官方数据的账本镜像（自动同步，与仓库数据独立）

## 目录结构

```
data_server.py            # 数据服务（HTTP/预热/缓存/API）
go-usage-widget.py        # 数据计算函数（用量/统计/历史/热力图/来源计价）
server_data.py            # 官方用量抓取与落库
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
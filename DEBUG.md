# Go 用量 Widget - 已知问题与恢复手册

> 本文件记录开发/使用中遇到的所有疑难问题的**根因**、**修复方式**与**恢复步骤**。
> 出问题时先查这里，按症状定位，避免重复排查。

---

## 1. 大窗点击穿透（只有左上角能点，其余点不到/穿透）

**症状**：窗口从小窗/中窗切换到 960×720 大窗后，只有左上角约原始尺寸（560×480）区域可点击，
7天/30天按钮、三个窗口按钮（─□×）、token 区点击无反应或穿透到背后窗口。

**根因**：WebView2 渲染内容铺满全窗（视觉正常、CDP 视口正常、Win32 控件矩形正常），
但 **WS_EX_LAYERED 分层窗口的 OS 命中测试（hit-test）区域在 resize 后不跟随新尺寸**，
仍停留在初始窗口大小。超出区域的点击被系统当作"透明像素"穿透。

**关键证据**：
- `diag_ctrl.py`：resize 后窗体/WebView2 子控件矩形全部 960×720 ✓
- `diag_hittest.py`：`WindowFromPoint` 在新区域命中 PowerShell（穿透）而非 Chrome Legacy Window
- 非 layered 诊断窗口 = 全区域可点 + 白色背景（反向证实是 layered 的 hit-test 问题）

**修复**（`go-usage-widget.py`）：resize 后同步翻转 WS_EX_LAYERED 强制系统重建分层表面：

```python
def _rebuild_layered_hit_test(win):
    hwnd = win.native.Handle.ToInt32()
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if ex & WS_EX_LAYERED:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
```

`Api.resize_window()` 在 `self._win.resize(w, h)` 后调用它。
`pylib/webview/platforms/winforms.py` 的 `resize()` 同时加了 SWP_FRAMECHANGED + SetBounds + Refresh
（SWP_FRAMECHANGED 单独无效，真正有效的是 toggle layered，勿删）。

**恢复**：若穿透复现，检查 `resize_window` 是否还调用 `_rebuild_layered_hit_test`；检查 winforms.py
`resize()` 是否仍保留（虽非主因，但辅助刷新无害）。

---

## 2. 透明度低时出现白底 / 灰底（数字越低越白）

**症状**：透明度滑到低值（<0.4）时，窗口背景/面板变成灰白色块，而非"更透明看到桌面"。

**根因（重要机制）**：WebView2 页面透明区域**实际显示的是窗体 BackColor**，不是桌面！
- WinForms 窗体 BackColor 默认 = 系统灰 `Control.DefaultBackColor` (240,240,240)
- 页面背景 `rgba(13,17,28,0.x)` 是**与窗体 BackColor 混合**的伪透明
- alpha 低 → 页面背景接近全透明 → 露出窗体 240 灰 = 白底

**修复**（`pylib/webview/platforms/winforms.py` transparent 分支）：窗体 BackColor 固定为深色：

```python
self.SetStyle(WinForms.ControlStyles.SupportsTransparentBackColor, True)
self.browser.DefaultBackgroundColor = Color.Transparent
self.BackColor = Color.FromArgb(255, 13, 17, 28)   # 固定深色, 禁止改回默认/Transparent
```

**效果**：透明度低时露出的永远是深色 (13,17,28)，呈现"深色半透明"，无白底。

**禁止事项**：
- ❌ 不要设 `Color.Transparent` 或 `Color.FromArgb(0,0,0,0)` → WinForms 会渲染成 240 灰
- ❌ 不要用 `SetLayeredWindowAttributes(LWA_ALPHA)` 做整窗透明 → 内容（文字/曲线）也跟着透明，
  用户明确否决（"字也要清晰可见"）

---

## 3. 透明度机制（当前正确方案）

**当前设计**（已定稿，勿随意改）：

| 层 | 变量 | 行为 |
|----|------|------|
| 窗口背景 | `.app` = `var(--bg)` = `rgba(13,17,28,var(--alpha))` | 跟随透明度滑块 |
| 面板/卡片 | `var(--glass)` = `rgba(24,30,46,calc(var(--alpha)*0.97))` | 跟随透明度（背景淡出） |
| 文字/曲线 | 固定色（--text/--dim/#818cf8 等） | **恒不透明**，滑块不影响 |
| 弹窗/菜单 | `var(--panel-2)` = `rgba(28,35,54,0.97)` | 保持不透明（可读性） |

- `--alpha` 由前端 `applyOpacity(v)` 设置（滑杆 input → `save_opacity` 存 config）
- 后端 `save_opacity`/`apply_opacity` 只存 config，**不做任何 Win32 透明调用**
- 窗体 BackColor 固定深色（见 #2），是页面透明区域的"底色"

**恢复**：若透明行为异常，检查：
1. `:root` 里 `--bg`/`--glass`/`--panel-2` 定义是否如上
2. winforms.py transparent 分支 `BackColor = Color.FromArgb(255, 13, 17, 28)`
3. 后端 `save_opacity` 无 `SetLayeredWindowAttributes` 调用

---

## 4. 模型明细 free 分组为空（大窗）

**症状**：大窗"模型明细"点 free 标签，列表为空；或"全部"只有 go 没有 free。

**根因**：`filteredStats()` 在 `selSupplier`（供应商 tab）非空时按 `source` 过滤模型，
free 模型的 source 是 `zen`/`kilo/router/zen`/`known` 等（**跨供应商共享**，不含 "go"），
被 `.includes(selSupplier)` 全部滤掉。

**修复**（`index.html` `filteredStats()`）：

```js
function filteredStats() {
  let st = state.stats;
  if (mFilter !== "all") st = st.filter(x => x.group === mFilter);
  return st;
}
```

模型列表只按 go/free/全部 分组，**供应商 tab 不参与模型过滤**（tab 只管热力图/统计卡/小窗 top 模型）。

**注意**：free 数量不是固定的！free 组 = 本地采集的免费模型（opencode.db 中 zen/kilo/router
供应商的真实数据 + `scan_free_models()` 扫描注入的未使用模型），随用户安装的供应商变化。
当前机器约 33 个模型（9 go + 24 free）只是参考值。

---

## 5. 检测/调试注意事项（重要教训）

**不要用 CDP 脚本反复改 `applyOpacity` 后不恢复**：
- 检测脚本 `applyOpacity(0.15)` 后若脚本超时/被杀，页面 `--alpha` 停在低值，
  用户看到"又坏了"（实际只是透明度停在最低）
- 恢复方法：CDP 执行 `applyOpacity(0.74)`，或重启 widget（启动时按 config 恢复）

**检测脚本要幂等**：结束时必须恢复原状态（alpha、selModel、heatRange、mFilter）。

**CDP 连接**（调试用）：
```powershell
$env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = "--remote-debugging-port=18765 --remote-allow-origins=*"
# websocket 握手必须带 Origin: http://127.0.0.1:18765，否则 403
```

**常用诊断脚本**（在 `%TEMP%\opencode\` 下）：
- `diag_hittest.py`：WindowFromPoint 验证命中区域
- `cdp_opacity.py`：控制透明度+屏幕采样
- `check_widget_hit.py`：真实 widget 命中验证

---

## 6. 其他已知事项

- **窗口标题**：`Go 用量`（FindWindowW 用）
- **透明 watcher**：`_enable_layered_watcher` 用 `range(60)`（30秒）非常驻——启动初期确保
  WS_EX_LAYERED 被加上即可，后续靠 `_rebuild_layered_hit_test` 维持
- **pylib 是 gitignore 的**：winforms.py 的修改只在本机生效，重新安装/覆盖 pylib 后需重打补丁：
  1. BackColor 固定深色（transparent 分支）
  2. resize() 加 SWP_FRAMECHANGED + SetBounds + PerformLayout + Refresh
- **config.json 含 auth_cookie，不提交**（gitignore 或手动排除）
- **诊断残留目录** `webdata_diag*/` 未提交（调试用数据）

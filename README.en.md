# OpenCode-widget

Floating transparent glass dashboard for OpenCode / Codex / Go usage — real-time OpenCode Go quota, per-model usage and cost.

![OpenCode-widget screenshot](screenshot.png)

![OpenCode-widget large dashboard](screenshot-large.png)

## Features

- **Transparent floating window**: Electron native transparent window with desktop see-through; text and SVG stay crisp, adjustable opacity
- **Three sizes**: compact (snapped Dock) / mid / full dashboard
- **Auto-compact on snap**: dragging to the top edge snaps and switches to the compact Dock (Mac Dock style), drag away to restore
- **Live usage**: rolling (5h) / weekly / monthly Go quota percentage + remaining time
- **Supplier tabs**: All / Go / Kilo / Zen / Router one-click switch, linked to model details, curves and heatmap
- **Model details**: grouped by All / Go / Free, supplier filter, usage ranking, real-time-axis history curves
- **Total curve**: switching to a supplier shows its aggregated total curve by default, no need to click each model
- **Free models**: aggregates free usage from all sources (router/zen/kilo/gateway and discovered unused models), never hidden by supplier filter
- **Full dashboard**: GitHub-style heatmap, stat cards (follow time range), input/output/cache split
- **Per-source pricing**: same model (e.g. hy3) gets independent free/paid status per supplier, matching official
- **Official data sync**: in-app login to opencode.ai grabs Cookie + workspace and syncs official quota values
- **Cloud formula auto-sync**: pulls cloud formula rules every 15 minutes (meter ratio / suppliers / view definitions), applies version changes immediately, keeps last good on failure
- **Official meter caliber**: totals shown at the official coefficient (1.4212), consistent with opencode.ai billing; details/curves keep raw billing
- **Hotkeys**: `Tab` switch supplier · `←/→` switch supplier · `↑/↓` switch model
- **Edge tooltip**: hover details flip to the left side at the right edge of the chart to avoid clipping

## Architecture

```
┌─────────────────────┐      HTTP/JSON       ┌──────────────────────────────┐
│  data_server.py     │ ───────────────────► │  Electron transparent window │
│  · reads local data │  127.0.0.1:8765      │  · renderer fetches directly │
│  · computes stats   │                      │  · window/snap/opacity        │
│  · preheat + cache  │                      │  · supplier/model/curve sync  │
│  · official sync    │                      └──────────────────────────────┘
└─────────────────────┘
```

- **Python only fetches and computes** (`data_server.py` + `go-usage-widget.py` data functions + `server_data.py` official sync + `views.py` cloud formula engine)
- **Frontend & window interaction go to Electron** (`electron/main.js` / `preload.js` / `app/index.html`)
- **Stealth launch**: spawns GUI processes directly (`pythonw.exe` + `electron.exe`) — no cmd/npm, no black console flash on double-click

## Quick start

### Stealth launch (recommended)

Double-click `启动Go用量悬浮窗.vbs` — no console window at all, and deduplicated (won't relaunch when already running):

```bat
启动Go用量悬浮窗.vbs
```

> Note: `.vbs` runs via `wscript.exe` and shows no console by default — the only truly zero-flash double-click entry.
> `.cmd` double-click makes Windows spawn cmd.exe, causing a brief black flash; kept as a compatibility entry.

### Manual launch

```bat
start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" data_server.py
start "" electron\node_modules\electron\dist\electron.exe electron
```

### Auto-start on boot (optional)

Put `启动Go用量悬浮窗.vbs` into the startup folder (Win+R → `shell:startup`), or create a desktop shortcut pointing to it.

### Dependencies

- Python 3.11 (built-in http.server, no third-party backend deps)
- Node.js + Electron (run `npm install` inside `electron/`)

## Usage

### Window forms

| Form | Trigger | Purpose |
|------|---------|---------|
| Compact | snap to top edge / minimize | snapped Dock, rolling quota only |
| Mid | default | quota + model detail list |
| Full | expand top-right | supplier tabs + heatmap + stat cards + curves + details |

### Supplier switching

Top tabs or hotkeys `←/→` (full) / `Tab` (any). After switching:
- auto returns to "All" filter and shows the supplier's **aggregated total curve** (`__total__`, not in the model list)
- model details, stat cards and heatmap follow the supplier

### Model details

- Top panel filters by **All / Go / Free** (Free aggregates free models from all sources)
- Click a model → selected; right-side curve/heatmap switches to it (Go/All mode also syncs the top supplier; Free mode does not)
- Click the selected model again → deselect, back to the supplier total curve
- Hotkeys `↑/↓` cycle models; pressing `↑` on the first model returns to the total curve

### Curve hover

Hover the history curve to show that day's Token / count / cost; at the right edge it auto-flips to the left of the cursor to avoid clipping.

### Compact time range (hotkey X)

The compact "Total usage" shows **today** by default. Press `X` to cycle: **today → last 7 days → all → today**. Main amount and bottom stat tiles (total Token / total cost / total count / active days) follow the range; choice persists in localStorage.

> Note: compact stats aggregate from per-day history, following the current supplier (or all); "all" means full history.

### Go quota reset countdown

With the Go supplier selected, the compact view shows an orange line between model stats and total Token: `⏳ Reset in 5 hours 0 minutes` — the remaining time of the Go platform official rolling quota window (Session/week/month mapped by current range: today→Session, 7 days→Week, all→Month).

### Login to sync official quota

Right-click menu → **Login with browser** (opens opencode.ai) or **Grab cookie & sync values** (in-app login window, auto-captures auth cookie + workspace_id). Login persists across restarts.

### Right-click menu

Refresh now / Login with browser / Grab cookie & sync / Server config (manual) / Manual calibration / API Key setup

## Data sources

- Local `~/.local/share/opencode/opencode.db` (OpenCode session usage) + `~/.codex/logs_2.sqlite`
- Official opencode.ai Go quota (after auth cookie config, synced via `POST /api/sync`)
- Local `server_usage.db` is the ledger mirror of official data (auto-synced, independent from repo data)
- Cloud formula (meter ratio / suppliers / view definitions) pulled from a Cloudflare Worker URL every 15 minutes; `views.py` ships a default formula as fallback

## Meter caliber

- **Totals / compact main amount** use the official caliber: billing cost × official meter ratio (currently 1.4212, defined by cloud formula `params.meter.ratio`, can change remotely), consistent with opencode.ai billing (capped by quota)
- **Model details / daily curves / tooltip** keep raw billing cost to avoid inflated detail rows
- Switching time range (today / 7 days / all) and supplier keeps the main amount in official caliber

## Directory structure

```
data_server.py            # data server (HTTP/preheat/cache/API)
go-usage-widget.py        # data computation (usage/stats/history/heatmap/per-source pricing)
server_data.py            # official usage fetch & persistence
views.py                  # cloud formula engine (fetch/cache/fallback + view aggregation & meter ratio)
browser_cookie.py         # browser cookie helper
启动Go用量悬浮窗.cmd      # compatibility launcher (flashes a black box once)
启动Go用量悬浮窗.vbs      # stealth launcher (recommended)
electron/
  main.js                 # transparent window/snap/size/login grab
  preload.js              # minimal IPC bridge
  app/index.html          # frontend UI
DEBUG.md                  # known issues & recovery manual
```

## Troubleshooting

For hard problems consult `DEBUG.md` first (documents root causes and recovery steps of past bugs).
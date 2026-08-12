const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');

const SIZES = {
  small: [540, 260],
  mid: [560, 480],
  large: [960, 720],
};

// 吸顶迟滞：靠近顶部 SNAP_NEAR 内触发吸顶；已吸顶时须拖离超过 SNAP_OUT 才解除。
// 否则吸顶状态下任何 12px 内的拖动都会被强制拽回顶部，窗口永远拖不下来。
const SNAP_NEAR = 12;
const SNAP_OUT = 40;

let win = null;
let snapped = false;
let curUiState = "mid";     // 前端最近一次上报的 UI 状态 (save-ui-state)
let preSnapUiState = "mid"; // 吸顶前的 UI 状态, 拖离时恢复

function restoreFromSnap() {
  if (!win) return;
  // 恢复目标: 吸顶期间用户若手动改过状态(如重新展开大屏)则尊重之, 否则恢复吸顶前状态
  const target = curUiState !== "small" ? curUiState : preSnapUiState;
  const size = SIZES[target] || SIZES.mid;
  const b = win.getBounds();
  win.setBounds({ x: b.x, y: b.y, width: size[0], height: size[1] });
  win.webContents.send('snap-restore', target);
}

function updateSnap(x, y) {
  if (!win) return;
  try {
    const wa2 = screen.getDisplayNearestPoint({ x, y }).workArea;
    const dy = y - wa2.y;
    if (snapped) {
      // 已吸顶：跟随拖动（不再强制回顶），拖离超过 SNAP_OUT 才解除并恢复中窗
      if (dy > SNAP_OUT) {
        snapped = false;
        restoreFromSnap();
      }
      return;
    }
    if (Math.abs(dy) <= SNAP_NEAR) {
      if (y !== wa2.y) win.setPosition(x, wa2.y);
      snapped = true;
      preSnapUiState = curUiState;
      const b = win.getBounds();
      win.setBounds({ x: b.x, y: wa2.y, width: SIZES.small[0], height: SIZES.small[1] });
      win.webContents.send('snap-small');
    }
  } catch (_) { /* ignore */ }
}

function createWindow() {
  const wa = screen.getPrimaryDisplay().workArea;
  win = new BrowserWindow({
    width: SIZES.mid[0],
    height: SIZES.mid[1],
    x: Math.round((wa.width - SIZES.mid[0]) / 2),
    y: Math.round((wa.height - SIZES.mid[1]) / 2),
    frame: false,
    transparent: true,
    resizable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    fullscreenable: false,
    maximizable: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.loadFile(path.join(__dirname, 'app', 'index.html'));

  // 吸顶：窗口顶边靠近屏幕工作区顶部时贴齐并通知前端切小屏
  const born = Date.now();
  win.on('move', () => {
    if (Date.now() - born < 2000) return;  // 启动 2 秒内不吸顶，避免初始化跳动触发
    const [x, y] = win.getPosition();
    updateSnap(x, y);
  });

  win.on('closed', () => { win = null; });
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  app.quit();
});

ipcMain.handle('resize', (e, uiState) => {
  const size = SIZES[uiState] || SIZES.mid;
  if (!win) return true;
  const bounds = win.getBounds();
  const wa = screen.getDisplayNearestPoint({ x: bounds.x, y: bounds.y }).workArea;
  let nx = bounds.x + Math.floor((bounds.width - size[0]) / 2);
  let ny = bounds.y + Math.floor((bounds.height - size[1]) / 2);
  // 吸顶保持：窗口顶边原本贴在工作区顶部时，缩放后仍贴顶（避免掉下来）
  if (Math.abs(bounds.y - wa.y) <= 12) ny = wa.y;
  // 防超出屏幕：窗口完整保持在工作区内（否则顶部被遮住无法拖动）
  nx = Math.max(wa.x, Math.min(nx, wa.x + wa.width - size[0]));
  ny = Math.max(wa.y, Math.min(ny, wa.y + wa.height - size[1]));
  win.setBounds({ x: nx, y: ny, width: size[0], height: size[1] });
  return true;
});

ipcMain.handle('quit', () => {
  app.quit();
  return true;
});

ipcMain.handle('open-login', async () => {
  try {
    const { shell } = require('electron');
    await shell.openExternal('https://opencode.ai/auth');
    return { opened: true };
  } catch (e) {
    return { opened: false, error: String(e) };
  }
});

ipcMain.handle('save-ui-state', (e, s) => { curUiState = s; return s; });

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

const DATA_URL = 'http://127.0.0.1:8765';

const SIZES = {
  small: [540, 260],
  mid: [560, 480],
  large: [960, 720],
};

let win = null;
let snapped = false;

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
    try {
      const [x, y] = win.getPosition();
      const wa2 = screen.getDisplayNearestPoint({ x, y }).workArea;
      const near = Math.abs(y - wa2.y) <= 12;
      if (near) {
        if (y !== wa2.y) win.setPosition(x, wa2.y);
        if (!snapped) {
          snapped = true;
          // 主进程直接改小屏尺寸（不依赖前端 IPC，确保吸顶即小屏）
          const size = SIZES.small;
          const b = win.getBounds();
          win.setBounds({ x: b.x, y: wa2.y, width: size[0], height: size[1] });
          win.webContents.send('snap-small');
        }
      } else {
        snapped = false;
      }
    } catch (_) { /* ignore */ }
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
  win.setBounds({ x: nx, y: ny, width: size[0], height: size[1] });
  return true;
});

ipcMain.handle('get-pos', () => {
  if (!win) return { x: 0, y: 0 };
  const [x, y] = win.getPosition();
  return { x, y };
});

ipcMain.handle('move-to', (e, x, y) => {
  if (!win) return true;
  const px = Math.round(x);
  const py = Math.round(y);
  const d = screen.getDisplayNearestPoint({ x: px, y: py });
  const wa = d.workArea;
  const SNAP = 12;
  let ny = py;
  // 吸顶：窗口顶边靠近屏幕工作区顶部时贴齐
  if (Math.abs(py - wa.y) <= SNAP) ny = wa.y;
  win.setPosition(px, ny);
  return true;
});

ipcMain.handle('set-opacity', (e, v) => {
  // 透明度由前端 CSS --alpha 控制（仅背景透明，文字/SVG 内容保持不透明）
  return true;
});

ipcMain.handle('quit', () => {
  app.quit();
  return true;
});

ipcMain.handle('fetch-state', async () => {
  const res = await fetch(DATA_URL + '/api/state');
  return res.json();
});

let loginWin = null;

ipcMain.handle('open-login', async () => {
  // 用 pywebview 原版 Console 登录（独立 pythonw 进程），登录后自动写 config + 同步
  const pyw = 'C:/Users/31807/AppData/Local/Programs/Python/Python311/pythonw.exe';
  const script = path.join(__dirname, '..', 'login_console.py');
  try {
    spawn(pyw, [script], { detached: true, stdio: 'ignore' }).unref();
    return { opened: true };
  } catch (e) {
    return { opened: false, error: String(e) };
  }
});

ipcMain.handle('grab-cookie', async () => {
  // cookie 由 login_console.py 直接写入 config.json；这里只返回配置状态供前端判断
  try {
    const res = await fetch(DATA_URL + '/api/config');
    const d = await res.json();
    return { cookie: (d.server || {}).auth_cookie || '', workspace_id: (d.server || {}).workspace_id || '' };
  } catch (_) {
    return { cookie: '', workspace_id: '' };
  }
});

ipcMain.handle('save-opacity', (e, v) => v);
ipcMain.handle('save-ui-state', (e, s) => s);

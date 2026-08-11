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

function createWindow() {
  win = new BrowserWindow({
    width: SIZES.mid[0],
    height: SIZES.mid[1],
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
  if (win) {
    const bounds = win.getBounds();
    win.setBounds({
      x: bounds.x + Math.floor((bounds.width - size[0]) / 2),
      y: bounds.y + Math.floor((bounds.height - size[1]) / 2),
      width: size[0],
      height: size[1],
    });
  }
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

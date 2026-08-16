const { app, BrowserWindow, ipcMain, screen, session } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');

// 临时诊断：吸顶判定日志（排查后移除）
const SNAP_LOG = path.join(os.tmpdir(), 'widget_snap.log');
function debugSnap(msg) {
  try { fs.appendFileSync(SNAP_LOG, new Date().toISOString() + ' ' + msg + '\n'); } catch (_) { /* ignore */ }
}

// 登录态分区：持久化，登录一次后续免登录复用
const LOGIN_PARTITION = 'persist:opencode-auth';
let loginWin = null;

function opencodeAuthUrl() {
  return 'https://opencode.ai/auth';
}

async function readAuthCookieFromSession(ses) {
  try {
    const cookies = await ses.cookies.get({ url: 'https://opencode.ai' });
    const auth = cookies.find(c => c.name === 'auth');
    return auth ? auth.value : '';
  } catch (_) { return ''; }
}

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
let pendingSnap = null;     // 拖动结束后要落定的状态: "snap" | "restore" | null
let programmaticMove = false; // 程序化移动(非用户拖拽)期间跳过吸顶判定，避免误吸顶

// 程序化 setBounds/setPosition：设置标记排除其引发的 move/moved 被当作用户拖拽
function progMove(fn) {
  programmaticMove = true;
  try { fn(); } finally { setTimeout(() => { programmaticMove = false; }, 150); }
}

// 吸顶态点击穿透：光标在顶栏内 → 窗口可拖可点；顶栏之下 → 点击穿透到背后桌面
let clickThroughMode = false;      // 是否处于吸顶穿透模式
let clickThroughHeaderH = 44;      // 顶栏高度（渲染层每次进吸顶时上报）
let clickThroughPoll = null;       // 光标轮询定时器
let clickThroughCur = false;       // 当前 ignoreMouseEvents 状态
let lastUserMoveAt = 0;            // 最近一次用户拖动时间戳（用于拖动中不切换穿透）
const CLICK_THROUGH_INTERVAL = 50;

function stopClickThroughPoll() {
  if (clickThroughPoll) { clearInterval(clickThroughPoll); clickThroughPoll = null; }
}

function setClickThrough(enabled, headerH) {
  clickThroughMode = !!enabled;
  if (typeof headerH === 'number' && headerH > 0) clickThroughHeaderH = headerH;
  if (clickThroughMode) {
    clickThroughCur = false;
    if (win) win.setIgnoreMouseEvents(false);
    if (!clickThroughPoll) clickThroughPoll = setInterval(pollClickThrough, CLICK_THROUGH_INTERVAL);
  } else {
    stopClickThroughPoll();
    clickThroughCur = false;
    if (win) win.setIgnoreMouseEvents(false);
  }
}

function pollClickThrough() {
  if (!clickThroughMode || !win || win.isDestroyed()) return;
  // 拖动进行中不切换穿透（避免打断原生拖拽）
  if (Date.now() - lastUserMoveAt < 150) return;
  try {
    const b = win.getBounds();
    const cp = screen.getCursorScreenPoint();
    const overHeader = cp.x >= b.x && cp.x <= b.x + b.width && cp.y >= b.y && cp.y <= b.y + clickThroughHeaderH;
    const want = !overHeader;
    if (want !== clickThroughCur) {
      clickThroughCur = want;
      win.setIgnoreMouseEvents(want, { forward: true });
    }
  } catch (_) { /* ignore */ }
}

function restoreFromSnap() {
  if (!win) return;
  // 恢复目标: 吸顶期间用户若手动改过状态(如重新展开大屏)则尊重之, 否则恢复吸顶前状态
  const target = curUiState !== "small" ? curUiState : preSnapUiState;
  const size = SIZES[target] || SIZES.mid;
  const b = win.getBounds();
  progMove(() => win.setBounds({ x: b.x, y: b.y, width: size[0], height: size[1] }));
  win.webContents.send('snap-restore', target);
}

// 拖动过程中绝不 setBounds/setPosition（Windows 下会中断原生拖拽，导致要拖两次）。
// 这里只记录"拖动结束后要做什么"，实际落定在 win.on('moved')。
function updateSnap(x, y) {
  if (!win) return;
  try {
    const wa2 = screen.getDisplayNearestPoint({ x, y }).workArea;
    const dy = y - wa2.y;
    if (snapped) {
      // 已吸顶：窗口自由跟随鼠标；拖离超过 SNAP_OUT 才解除（落定在 moved）
      if (dy > SNAP_OUT) { snapped = false; pendingSnap = "restore"; debugSnap('arm-restore dy=' + dy); }
      else pendingSnap = null; // 未拖出阈值，松手仍保持吸顶
      return;
    }
    // 本次拖动已决定解除吸顶后，不再因回顶部而翻盘吸顶
    if (pendingSnap === "restore") return;
    if (Math.abs(dy) <= SNAP_NEAR) { pendingSnap = "snap"; preSnapUiState = curUiState; debugSnap('arm-snap dy=' + dy); }
    else if (pendingSnap === "snap") { pendingSnap = null; debugSnap('disarm-snap dy=' + dy); }
  } catch (_) { /* ignore */ }
}

// 拖拽结束(moved)时落定，此刻 setBounds 安全，不会中断拖拽
function applyPendingSnap() {
  if (!win || !pendingSnap) return;
  const p = pendingSnap;
  pendingSnap = null;
  try {
    const [x, y] = win.getPosition();
    const wa = screen.getDisplayNearestPoint({ x, y }).workArea;
    const dy = y - wa.y;
    if (p === "snap") {
      // 落定时复验：moved 若在拖动中途触发，窗口可能已不在吸顶带内，此时放弃吸顶
      if (Math.abs(dy) > SNAP_NEAR) { debugSnap('snap-cancel dy=' + dy); return; }
      progMove(() => win.setBounds({ x, y: wa.y, width: SIZES.small[0], height: SIZES.small[1] }));
      snapped = true;
      debugSnap('snap dy=' + dy);
      win.webContents.send('snap-small');
    } else if (p === "restore") {
      debugSnap('restore dy=' + dy);
      restoreFromSnap();
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
    if (programmaticMove) return;          // 程序化移动不做吸顶判定
    lastUserMoveAt = Date.now();
    const [x, y] = win.getPosition();
    updateSnap(x, y);
  });

  // 拖拽结束（Windows 上 move 事件密集，moved 在真正停手后触发一次）才落定吸顶/解除，
  // 避免拖动中途 setBounds 中断原生拖拽（否则一次拖不完、要拖两次）。
  win.on('moved', () => {
    debugSnap('moved fired, pendingSnap=' + pendingSnap + ' pos=' + JSON.stringify(win.getPosition()));
    applyPendingSnap();
  });

  win.on('closed', () => {
    stopClickThroughPoll();
    win = null;
  });

  win.setIgnoreMouseEvents(false);
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
  progMove(() => win.setBounds({ x: nx, y: ny, width: size[0], height: size[1] }));
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

// 应用内登录窗口：自动抓取 auth cookie + workspace_id（替代 F12 手动复制）
ipcMain.handle('grab-auth', async () => {
  // 若已有有效登录态（分区 cookie + 自动重定向）则直接捕获，无需用户操作；
  // 否则停留在登录页等用户登录后捕获。同一分区持久化，下次免登录。
  if (loginWin) { loginWin.focus(); return { ok: false, error: '登录窗口已打开' }; }
  const ses = session.fromPartition(LOGIN_PARTITION);
  return new Promise((resolve) => {
    let settled = false;
    let poll = null;
    const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };
    const settle = (r) => { if (!settled) { settled = true; stopPoll(); resolve(r); } };
    loginWin = new BrowserWindow({
      width: 980, height: 700,
      parent: win,
      modal: true,
      resizable: true,
      autoHideMenuBar: true,
      backgroundColor: '#0b0e14',
      title: 'opencode.ai 登录',
      webPreferences: {
        session: ses,
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    loginWin.loadURL(opencodeAuthUrl());
    loginWin.on('closed', () => {
      stopPoll();
      loginWin = null;
      settle({ ok: false, error: '登录窗口已关闭' });
    });

    const tryCapture = async () => {
      if (settled || !loginWin || loginWin.isDestroyed()) return;
      try {
        const url = loginWin.webContents.getURL();
        const m = /\/workspace\/(wrk_[A-Za-z0-9]+)/.exec(url);
        if (!m) return;
        const cookie = await readAuthCookieFromSession(ses);
        if (!cookie) return;
        settle({ ok: true, auth_cookie: cookie, workspace_id: m[1] });
        setTimeout(() => { if (loginWin && !loginWin.isDestroyed()) loginWin.close(); }, 300);
      } catch (_) { /* 继续等待 */ }
    };
    loginWin.webContents.on('did-navigate', tryCapture);
    loginWin.webContents.on('did-redirect-navigation', tryCapture);
    loginWin.webContents.on('did-finish-load', tryCapture);
    // 兜底：SPA 或延迟重定向时轮询
    poll = setInterval(tryCapture, 800);
  });
});

ipcMain.handle('save-ui-state', (e, s) => { curUiState = s; return s; });

// 吸顶态点击穿透：enabled=true 时启用光标轮询（顶栏内可交互，顶栏下穿透到桌面）
ipcMain.handle('set-click-through', (_e, enabled, headerH) => {
  setClickThrough(enabled, headerH);
  return true;
});

// 前端手动退出吸顶（如吸顶态点击 _ / □ 展开）：清空吸顶态并关闭穿透
ipcMain.handle('exit-snap', () => {
  snapped = false;
  pendingSnap = null;
  setClickThrough(false);
  return true;
});

const { contextBridge, ipcRenderer } = require('electron');

// token 由主进程读取 runtime/token.txt，再通过同步 IPC 返回。
// 这样 preload 不需要 Node 文件系统权限，renderer sandbox 可以保持开启。
function getApiToken() {
  try {
    return ipcRenderer.sendSync('get-api-token') || '';
  } catch (_) {
    return '';
  }
}

contextBridge.exposeInMainWorld('widgetAPI', {
  getToken: getApiToken,
  resize: (uiState) => ipcRenderer.invoke('resize', uiState),
  openLogin: () => ipcRenderer.invoke('open-login'),
  grabAuth: () => ipcRenderer.invoke('grab-auth'),
  saveUiState: (s) => ipcRenderer.invoke('save-ui-state', s),
  onSnapSmall: (cb) => ipcRenderer.on('snap-small', () => cb()),
  onSnapRestore: (cb) => ipcRenderer.on('snap-restore', (_e, s) => cb(s)),
  setClickThrough: (enabled, headerH) => ipcRenderer.invoke('set-click-through', enabled, headerH),
  exitSnap: () => ipcRenderer.invoke('exit-snap'),
  quit: () => ipcRenderer.invoke('quit'),
});

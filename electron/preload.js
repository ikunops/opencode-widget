const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('widgetAPI', {
  resize: (uiState) => ipcRenderer.invoke('resize', uiState),
  getPos: () => ipcRenderer.invoke('get-pos'),
  moveTo: (x, y) => ipcRenderer.invoke('move-to', x, y),
  setOpacity: (v) => ipcRenderer.invoke('set-opacity', v),
  fetchState: () => ipcRenderer.invoke('fetch-state'),
  openLogin: () => ipcRenderer.invoke('open-login'),
  grabCookie: () => ipcRenderer.invoke('grab-cookie'),
  saveOpacity: (v) => ipcRenderer.invoke('save-opacity', v),
  saveUiState: (s) => ipcRenderer.invoke('save-ui-state', s),
  onSnapSmall: (cb) => ipcRenderer.on('snap-small', () => cb()),
  quit: () => ipcRenderer.invoke('quit'),
});

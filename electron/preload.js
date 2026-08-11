const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('widgetAPI', {
  resize: (uiState) => ipcRenderer.invoke('resize', uiState),
  setOpacity: (v) => ipcRenderer.invoke('set-opacity', v),
  fetchState: () => ipcRenderer.invoke('fetch-state'),
  openLogin: () => ipcRenderer.invoke('open-login'),
  grabCookie: () => ipcRenderer.invoke('grab-cookie'),
  saveOpacity: (v) => ipcRenderer.invoke('save-opacity', v),
  saveUiState: (s) => ipcRenderer.invoke('save-ui-state', s),
  quit: () => ipcRenderer.invoke('quit'),
});

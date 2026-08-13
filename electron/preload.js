const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('widgetAPI', {
  resize: (uiState) => ipcRenderer.invoke('resize', uiState),
  openLogin: () => ipcRenderer.invoke('open-login'),
  grabAuth: () => ipcRenderer.invoke('grab-auth'),
  saveUiState: (s) => ipcRenderer.invoke('save-ui-state', s),
  onSnapSmall: (cb) => ipcRenderer.on('snap-small', () => cb()),
  onSnapRestore: (cb) => ipcRenderer.on('snap-restore', (_e, s) => cb(s)),
  quit: () => ipcRenderer.invoke('quit'),
});

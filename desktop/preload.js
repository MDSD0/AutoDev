// desktop/preload.js — AutoDev Desktop Bridge API
// Exposes safe IPC channels to the renderer process.

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  // ── App control ─────────────────────────────────────
  quit: () => ipcRenderer.send('quit-app'),
  platform: process.platform,
  arch: process.arch,
  isDesktop: true,

  // ── Setup wizard ────────────────────────────────────
  detectOllama: () => ipcRenderer.invoke('detect-ollama'),
  detectPython: () => ipcRenderer.invoke('detect-python'),
  getSetupStatus: () => ipcRenderer.invoke('get-setup-status'),
  saveApiKeys: (keys) => ipcRenderer.invoke('save-api-keys', keys),
  requestTerminalAccess: () => ipcRenderer.invoke('request-terminal-access'),
  completeSetup: () => ipcRenderer.invoke('complete-setup'),
  setupPythonEnv: () => ipcRenderer.invoke('setup-python-env'),
  pullOllamaModel: (model) => ipcRenderer.invoke('pull-ollama-model', model),
  testProvider: (provider, apiKey) => ipcRenderer.invoke('test-provider', provider, apiKey),

  // ── Navigation ──────────────────────────────────────
  navigateToApp: () => ipcRenderer.invoke('navigate-to-app'),
  getBackendUrl: () => ipcRenderer.invoke('get-backend-url'),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),

  // ── Event listeners ─────────────────────────────────
  onBackendReady: (callback) => {
    ipcRenderer.on('backend-ready', (_, data) => callback(data));
  },
  onSetupProgress: (callback) => {
    ipcRenderer.on('setup-progress', (_, data) => callback(data));
  },
  removeAllListeners: (channel) => {
    ipcRenderer.removeAllListeners(channel);
  },
});

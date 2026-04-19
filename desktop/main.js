// desktop/main.js — AutoDev Desktop Electron Main Process
// Handles Python backend lifecycle, first-run setup, and IPC.

const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const { spawn, execSync, execFile } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const os = require('os');

// ── Paths ─────────────────────────────────────────────────
const IS_DEV = process.argv.includes('--dev') || !app.isPackaged;
const PROJECT_ROOT = IS_DEV
  ? path.resolve(__dirname, '..')
  : path.join(process.resourcesPath, 'app');
const FRONTEND_DIR = path.join(PROJECT_ROOT, 'frontend');
const AUTODEV_HOME = path.join(os.homedir(), '.autodev');
const RUNTIME_SETTINGS_PATH = path.join(PROJECT_ROOT, '.autodev_runtime_settings.json');
const VENV_DIR = path.join(AUTODEV_HOME, 'venv');
const BACKEND_PORT = 8000;
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// ── State ─────────────────────────────────────────────────
let mainWindow = null;
let backendProcess = null;
let isBackendReady = false;
let setupComplete = false;

// ── Ensure directories exist ──────────────────────────────
function ensureDirs() {
  [AUTODEV_HOME].forEach(dir => {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  });
}

// ── Python detection ──────────────────────────────────────
function findPython() {
  const candidates = process.platform === 'win32'
    ? ['python', 'python3', 'py']
    : ['python3', 'python'];
  for (const cmd of candidates) {
    try {
      const version = execSync(`${cmd} --version 2>&1`, { encoding: 'utf-8', timeout: 5000 }).trim();
      if (version.includes('Python 3')) {
        return { command: cmd, version };
      }
    } catch (_) { /* next */ }
  }
  return null;
}

// ── Venv management ───────────────────────────────────────
function venvPython() {
  return process.platform === 'win32'
    ? path.join(VENV_DIR, 'Scripts', 'python.exe')
    : path.join(VENV_DIR, 'bin', 'python3');
}

function venvExists() {
  return fs.existsSync(venvPython());
}

function createVenv(pythonCmd) {
  return new Promise((resolve, reject) => {
    const proc = spawn(pythonCmd, ['-m', 'venv', VENV_DIR], {
      stdio: 'pipe',
      env: { ...process.env },
    });
    let stderr = '';
    proc.stderr?.on('data', d => stderr += d.toString());
    proc.on('close', code => {
      if (code === 0) resolve();
      else reject(new Error(`venv creation failed (code ${code}): ${stderr}`));
    });
    proc.on('error', reject);
  });
}

function installRequirements() {
  return new Promise((resolve, reject) => {
    const reqFile = path.join(PROJECT_ROOT, 'requirements.txt');
    if (!fs.existsSync(reqFile)) {
      return resolve(); // No requirements to install
    }
    const pip = process.platform === 'win32'
      ? path.join(VENV_DIR, 'Scripts', 'pip.exe')
      : path.join(VENV_DIR, 'bin', 'pip');
    const proc = spawn(pip, ['install', '-r', reqFile, '--quiet'], {
      stdio: 'pipe',
      env: { ...process.env, VIRTUAL_ENV: VENV_DIR },
    });
    let stderr = '';
    proc.stderr?.on('data', d => stderr += d.toString());
    proc.on('close', code => {
      if (code === 0) resolve();
      else reject(new Error(`pip install failed (code ${code}): ${stderr}`));
    });
    proc.on('error', reject);
  });
}

// ── Runtime settings ──────────────────────────────────────
function loadRuntimeSettings() {
  try {
    if (fs.existsSync(RUNTIME_SETTINGS_PATH)) {
      return JSON.parse(fs.readFileSync(RUNTIME_SETTINGS_PATH, 'utf-8'));
    }
  } catch (_) { /* ignore */ }
  return null;
}

function saveRuntimeSettings(settings) {
  settings.updatedAt = new Date().toISOString();
  fs.writeFileSync(RUNTIME_SETTINGS_PATH, JSON.stringify(settings, null, 2), 'utf-8');
}

function isSetupComplete() {
  const settings = loadRuntimeSettings();
  return settings?.setupCompleted === true;
}

// ── Ollama detection ──────────────────────────────────────
function detectOllama() {
  return new Promise((resolve) => {
    try {
      const result = execSync('ollama list 2>&1', { encoding: 'utf-8', timeout: 10000 });
      const lines = result.split('\n').filter(l => l.trim());
      const models = lines.slice(1).map(line => {
        const parts = line.split(/\s+/);
        return { name: parts[0] || '', raw: line.trim() };
      }).filter(m => m.name);
      resolve({ installed: true, running: true, models });
    } catch (err) {
      // Check if ollama binary exists
      try {
        execSync('which ollama 2>&1 || where ollama 2>&1', { encoding: 'utf-8', timeout: 5000 });
        resolve({ installed: true, running: false, models: [] });
      } catch (_) {
        resolve({ installed: false, running: false, models: [] });
      }
    }
  });
}

// ── Backend lifecycle ─────────────────────────────────────
function startBackend() {
  return new Promise((resolve, reject) => {
    const pythonBin = venvExists() ? venvPython() : (findPython()?.command || 'python3');
    const parentDir = path.resolve(PROJECT_ROOT, '..');
    const env = {
      ...process.env,
      AUTODEV_DESKTOP: '1',
      PYTHONUNBUFFERED: '1',
      VIRTUAL_ENV: venvExists() ? VENV_DIR : '',
      PATH: venvExists()
        ? `${path.dirname(venvPython())}${path.delimiter}${process.env.PATH}`
        : process.env.PATH,
    };

    backendProcess = spawn(pythonBin, [
      '-m', 'uvicorn', 'autodev.server:app',
      '--host', '127.0.0.1',
      '--port', String(BACKEND_PORT),
      '--log-level', 'info',
    ], {
      cwd: parentDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env,
      detached: false,
    });

    let startupOutput = '';
    backendProcess.stdout?.on('data', d => {
      startupOutput += d.toString();
      console.log('[backend]', d.toString().trim());
    });
    backendProcess.stderr?.on('data', d => {
      startupOutput += d.toString();
      console.log('[backend-err]', d.toString().trim());
    });
    backendProcess.on('error', err => {
      console.error('[backend] Failed to start:', err);
      reject(err);
    });
    backendProcess.on('close', code => {
      console.log('[backend] Exited with code', code);
      backendProcess = null;
      isBackendReady = false;
    });

    // Poll for readiness
    let attempts = 0;
    let resolved = false;
    const MAX_ATTEMPTS = 60; // 30 seconds
    const poll = () => {
      if (resolved) return;
      attempts++;
      const req = http.get(`${BACKEND_URL}/ready`, (res) => {
        if (res.statusCode === 200 && !resolved) {
          resolved = true;
          isBackendReady = true;
          console.log('[backend] Ready after', attempts, 'polls');
          resolve();
        } else if (!resolved) {
          retry();
        }
      });
      req.on('error', () => { if (!resolved) retry(); });
      req.setTimeout(2000, () => { req.destroy(); if (!resolved) retry(); });
    };
    const retry = () => {
      if (resolved) return;
      if (attempts >= MAX_ATTEMPTS) {
        resolved = true;
        reject(new Error(`Backend failed to start after ${MAX_ATTEMPTS} attempts.\n${startupOutput.slice(-500)}`));
        return;
      }
      setTimeout(poll, 500);
    };
    // Give it a moment to start before polling
    setTimeout(poll, 1000);
  });
}

function stopBackend() {
  if (backendProcess) {
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t']);
      } else {
        backendProcess.kill('SIGTERM');
        setTimeout(() => {
          try { backendProcess?.kill('SIGKILL'); } catch (_) { }
        }, 3000);
      }
    } catch (_) { }
    backendProcess = null;
  }
}

// ── Window creation ───────────────────────────────────────
function createWindow(page = 'index.html') {
  const isMac = process.platform === 'darwin';
  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 900,
    minHeight: 600,
    title: 'AutoDev',
    titleBarStyle: isMac ? 'hiddenInset' : 'default',
    trafficLightPosition: isMac ? { x: 16, y: 16 } : undefined,
    backgroundColor: '#0a0a0f',
    icon: path.join(__dirname, 'icons', 'icon.png'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  if (page === 'setup.html') {
    mainWindow.loadFile(path.join(FRONTEND_DIR, 'setup.html'));
  } else {
    mainWindow.loadURL(`${BACKEND_URL}/`);
  }

  mainWindow.on('closed', () => { mainWindow = null; });
  return mainWindow;
}

// ── IPC Handlers ──────────────────────────────────────────
function registerIpcHandlers() {
  ipcMain.handle('detect-ollama', async () => {
    return await detectOllama();
  });

  ipcMain.handle('detect-python', async () => {
    const systemPython = findPython();
    const hasVenv = venvExists();
    return {
      systemPython: systemPython ? systemPython : null,
      venvExists: hasVenv,
      venvPath: VENV_DIR,
    };
  });

  ipcMain.handle('save-api-keys', async (_, keys) => {
    try {
      let settings = loadRuntimeSettings() || getDefaultSettings();
      if (keys.geminiApiKey) settings.secrets.geminiApiKey = keys.geminiApiKey;
      if (keys.groqApiKey) settings.secrets.groqApiKey = keys.groqApiKey;
      if (keys.groqApiKey2) settings.secrets.groqApiKey2 = keys.groqApiKey2;
      if (keys.huggingFaceApiKey) settings.secrets.huggingFaceApiKey = keys.huggingFaceApiKey;
      if (keys.ollamaBaseUrl) {
        settings.secrets.ollamaBaseUrl = keys.ollamaBaseUrl;
        settings.local_models = settings.local_models || {};
        settings.local_models.ollamaBaseUrl = keys.ollamaBaseUrl;
      }
      if (keys.defaultProvider) {
        settings.provider_registry = settings.provider_registry || {};
        settings.provider_registry.defaultProvider = keys.defaultProvider;
        settings.workflow_defaults = settings.workflow_defaults || {};
        settings.workflow_defaults.defaultProvider = keys.defaultProvider;
      }
      if (keys.ollamaModel) {
        settings.local_models = settings.local_models || {};
        settings.local_models.ollamaModel = keys.ollamaModel;
      }
      saveRuntimeSettings(settings);
      // If backend is running, also push to backend
      if (isBackendReady) {
        try {
          const postData = JSON.stringify({ settings });
          const options = {
            hostname: '127.0.0.1', port: BACKEND_PORT,
            path: '/settings/runtime', method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postData) },
          };
          await new Promise((resolve) => {
            const req = http.request(options, resolve);
            req.on('error', resolve);
            req.write(postData);
            req.end();
          });
        } catch (_) { }
      }
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  ipcMain.handle('request-terminal-access', async () => {
    if (process.platform === 'darwin') {
      // On macOS, try running a simple subprocess to see if we have access
      try {
        execSync('echo "AutoDev terminal access test"', { encoding: 'utf-8', timeout: 5000 });
        return { granted: true, platform: 'darwin' };
      } catch (_) {
        // Open System Preferences to Accessibility
        shell.openExternal('x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility');
        return { granted: false, platform: 'darwin', message: 'Please grant Accessibility access in System Preferences.' };
      }
    }
    // Windows and Linux don't need special permissions
    return { granted: true, platform: process.platform };
  });

  ipcMain.handle('get-setup-status', async () => {
    const pythonInfo = findPython();
    const ollamaInfo = await detectOllama();
    const settings = loadRuntimeSettings();
    return {
      setupCompleted: settings?.setupCompleted === true,
      pythonDetected: !!pythonInfo,
      pythonVersion: pythonInfo?.version || null,
      venvExists: venvExists(),
      ollamaInstalled: ollamaInfo.installed,
      ollamaRunning: ollamaInfo.running,
      ollamaModels: ollamaInfo.models,
      hasApiKeys: !!(settings?.secrets?.geminiApiKey || settings?.secrets?.groqApiKey),
      platform: process.platform,
      arch: process.arch,
    };
  });

  ipcMain.handle('complete-setup', async () => {
    let settings = loadRuntimeSettings() || getDefaultSettings();
    settings.setupCompleted = true;
    settings.setupCompletedAt = new Date().toISOString();
    settings.desktopMode = true;
    saveRuntimeSettings(settings);
    setupComplete = true;

    // Start backend if not running
    if (!isBackendReady) {
      mainWindow?.webContents?.send('setup-progress', { step: 'backend', message: 'Starting AutoDev engine...' });
      try {
        await ensurePythonEnvironment();
        await startBackend();
        mainWindow?.webContents?.send('backend-ready', { url: BACKEND_URL });
      } catch (err) {
        return { success: false, error: err.message };
      }
    }
    return { success: true, backendUrl: BACKEND_URL };
  });

  ipcMain.handle('get-backend-url', () => BACKEND_URL);

  ipcMain.handle('open-external', async (_, url) => {
    await shell.openExternal(url);
  });

  ipcMain.handle('setup-python-env', async () => {
    try {
      await ensurePythonEnvironment();
      return { success: true };
    } catch (err) {
      return { success: false, error: err.message };
    }
  });

  ipcMain.handle('navigate-to-app', async () => {
    if (mainWindow && isBackendReady) {
      mainWindow.loadURL(`${BACKEND_URL}/`);
      return { success: true };
    }
    return { success: false };
  });

  ipcMain.handle('pull-ollama-model', async (_, modelName) => {
    return new Promise((resolve) => {
      try {
        const proc = spawn('ollama', ['pull', modelName], { stdio: 'pipe' });
        let output = '';
        proc.stdout?.on('data', d => {
          output += d.toString();
          mainWindow?.webContents?.send('setup-progress', {
            step: 'ollama-pull',
            message: `Pulling ${modelName}... ${d.toString().trim()}`,
          });
        });
        proc.stderr?.on('data', d => output += d.toString());
        proc.on('close', code => {
          resolve({ success: code === 0, output: output.trim() });
        });
        proc.on('error', err => resolve({ success: false, error: err.message }));
      } catch (err) {
        resolve({ success: false, error: err.message });
      }
    });
  });

  ipcMain.handle('test-provider', async (_, provider, apiKey) => {
    // Quick test by making a backend call
    if (!isBackendReady) {
      // Direct test without backend
      if (provider === 'ollama') {
        const info = await detectOllama();
        return { success: info.running, message: info.running ? 'Ollama is running' : 'Ollama is not running' };
      }
      return { success: !!apiKey, message: apiKey ? 'API key provided (will verify on launch)' : 'No API key provided' };
    }
    try {
      const postData = JSON.stringify({ provider });
      return await new Promise((resolve) => {
        const req = http.request({
          hostname: '127.0.0.1', port: BACKEND_PORT,
          path: '/settings/test_provider', method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(postData) },
        }, (res) => {
          let body = '';
          res.on('data', d => body += d);
          res.on('end', () => {
            try {
              const data = JSON.parse(body);
              resolve({ success: data.status === 'ready', message: data.message || data.status });
            } catch (_) {
              resolve({ success: false, message: 'Invalid response' });
            }
          });
        });
        req.on('error', err => resolve({ success: false, message: err.message }));
        req.write(postData);
        req.end();
      });
    } catch (err) {
      return { success: false, message: err.message };
    }
  });
}

function getDefaultSettings() {
  return {
    version: 2,
    workflow_defaults: {
      defaultProvider: 'auto',
      workflowDepth: 'balanced',
      mode: 'auto',
      refinePrompt: true,
      maxRetries: 6,
      expertMode: false,
      skipSpec: false,
      skipReview: false,
    },
    provider_registry: {
      defaultProvider: 'auto',
      providers: {
        auto: { enabled: true, selectedModel: 'auto' },
        gemini: { enabled: true, selectedModel: 'gemini' },
        groq: { enabled: true, selectedModel: 'groq' },
        groq_2: { enabled: true, selectedModel: 'groq_2' },
        ollama: { enabled: true, selectedModel: 'ollama' },
        custom: { enabled: true, selectedModel: 'custom' },
      },
      providerStates: {},
      roleAssignments: {
        router: { provider: 'auto', model: 'auto' },
        planner: { provider: 'gemini', model: 'gemini' },
        coder: { provider: 'groq', model: 'groq' },
        reviewer: { provider: 'auto', model: 'auto' },
        executor: { provider: 'groq', model: 'groq' },
      },
    },
    secrets: {
      geminiApiKey: '',
      groqApiKey: '',
      groqApiKey2: '',
      huggingFaceApiKey: '',
      ollamaBaseUrl: 'http://localhost:11434',
    },
    local_models: {
      sourceType: 'ollama',
      ollamaModel: 'qwen2.5-coder:3b',
      ollamaBaseUrl: 'http://localhost:11434',
      huggingFaceUrl: '',
      modelFilePath: '',
      llamaCppCommand: 'llama-server',
      llamaCppPort: 8001,
      llamaCppContext: 4096,
      llamaCppPid: null,
      llamaCppStatus: 'stopped',
      hfDownloadPattern: '*.gguf',
      hfLocalDir: path.join(AUTODEV_HOME, 'local_models'),
      localEndpointUrl: '',
      localEndpointModel: 'local-model',
      localEndpointApiKey: '',
      localEndpointAuthHeader: 'Authorization',
      selectedLocalProvider: 'ollama',
    },
    customEndpoints: [],
    setupCompleted: false,
    desktopMode: true,
  };
}

// ── Python environment setup ──────────────────────────────
let _pythonEnvReady = false;
async function ensurePythonEnvironment() {
  if (_pythonEnvReady) return;
  const pythonInfo = findPython();
  if (!pythonInfo) {
    throw new Error('Python 3 is not installed. Please install Python 3.10 or later and restart AutoDev.');
  }

  if (!venvExists()) {
    console.log('[setup] Creating Python virtual environment...');
    mainWindow?.webContents?.send('setup-progress', { step: 'venv', message: 'Creating Python environment...' });
    await createVenv(pythonInfo.command);
  }

  // Install requirements
  console.log('[setup] Installing Python dependencies...');
  mainWindow?.webContents?.send('setup-progress', { step: 'deps', message: 'Installing dependencies (this may take a minute)...' });
  await installRequirements();
  _pythonEnvReady = true;
}

// ── App lifecycle ─────────────────────────────────────────
app.whenReady().then(async () => {
  ensureDirs();
  registerIpcHandlers();

  setupComplete = isSetupComplete();

  if (!setupComplete) {
    // Show setup wizard
    createWindow('setup.html');
  } else {
    // Normal launch — start backend, then show main app
    const splash = createWindow('setup.html'); // Show loading screen
    try {
      await ensurePythonEnvironment();
      splash?.webContents?.send('setup-progress', { step: 'backend', message: 'Starting AutoDev engine...' });
      await startBackend();
      // Navigate to main app
      if (mainWindow) {
        mainWindow.loadURL(`${BACKEND_URL}/`);
      }
    } catch (err) {
      console.error('[startup] Failed:', err);
      dialog.showErrorBox('AutoDev Startup Error', err.message);
    }
  }
});

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) {
    if (setupComplete && isBackendReady) {
      createWindow('index.html');
    }
  }
});

app.on('before-quit', () => {
  stopBackend();
});

app.on('quit', () => {
  stopBackend();
});

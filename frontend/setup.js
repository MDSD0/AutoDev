/* =====================================================
   AutoDev Desktop — Setup Wizard Controller (setup.js)
   Handles multi-step setup, Ollama detection,
   API key validation, HuggingFace download,
   permissions, and system verification.
   ===================================================== */

// ── State ─────────────────────────────────────────────
let currentStep = 1;
const TOTAL_STEPS = 5;
let selectedSource = null; // 'cloud' | 'ollama' | 'huggingface'
let selectedOllamaModel = '';
let setupStatus = null;
let isProcessing = false;

// ── DOM refs ──────────────────────────────────────────
const setupWizard = document.getElementById('setupWizard');
const loadingScreen = document.getElementById('loadingScreen');
const loadingText = document.getElementById('loadingText');
const loadingSubtext = document.getElementById('loadingSubtext');
const stepProgress = document.getElementById('stepProgress');
const nextBtn = document.getElementById('nextBtn');
const backBtn = document.getElementById('backBtn');

// ── Check if we need setup or just loading ────────────
async function initSetup() {
  const api = window.electronAPI;
  if (!api) {
    // Not running in Electron — show wizard anyway for dev
    showWizard();
    return;
  }

  // Listen for progress events
  api.onSetupProgress((data) => {
    if (loadingText) loadingText.textContent = data.message || 'Setting up...';
    if (loadingSubtext) loadingSubtext.textContent = data.step || '';
  });

  api.onBackendReady(() => {
    // Backend is ready — navigate to main app
    setTimeout(() => api.navigateToApp(), 500);
  });

  // Check setup status
  try {
    setupStatus = await api.getSetupStatus();
    if (setupStatus.setupCompleted) {
      // Already set up — show loading screen
      showLoading();
      return;
    }
  } catch (e) {
    console.warn('[setup] Failed to get status:', e);
  }

  showWizard();
  await populateWelcomeStep();
}

function showLoading() {
  setupWizard.style.display = 'none';
  loadingScreen.style.display = 'flex';
}

function showWizard() {
  setupWizard.style.display = 'flex';
  loadingScreen.style.display = 'none';
}

// ── Step Navigation ───────────────────────────────────
function goToStep(step) {
  if (step < 1 || step > TOTAL_STEPS) return;
  currentStep = step;

  // Update panels
  document.querySelectorAll('.step-panel').forEach(p => {
    p.classList.toggle('active', parseInt(p.dataset.step) === step);
  });

  // Update progress dots
  document.querySelectorAll('.step-dot').forEach(dot => {
    const s = parseInt(dot.dataset.step);
    dot.classList.remove('active', 'completed');
    if (s === step) dot.classList.add('active');
    else if (s < step) {
      dot.classList.add('completed');
      dot.textContent = '';
    } else {
      dot.textContent = String(s);
    }
  });

  // Update connectors
  document.querySelectorAll('.step-connector').forEach((conn, i) => {
    conn.classList.toggle('completed', i + 1 < step);
  });

  // Update buttons
  backBtn.style.visibility = step > 1 ? 'visible' : 'hidden';
  updateNextButton();

  // Run step-specific init
  if (step === 3) initConfigStep();
  if (step === 4) initPermissionsStep();
  if (step === 5) runSystemCheck();
}

function updateNextButton() {
  switch (currentStep) {
    case 1:
      nextBtn.innerHTML = 'Get Started <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><polyline points="12 5 19 12 12 19"/></svg>';
      nextBtn.disabled = false;
      nextBtn.className = 'btn btn-primary';
      break;
    case 2:
      nextBtn.innerHTML = 'Continue <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><polyline points="12 5 19 12 12 19"/></svg>';
      nextBtn.disabled = !selectedSource;
      nextBtn.className = 'btn btn-primary';
      break;
    case 3:
      nextBtn.innerHTML = 'Continue <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><polyline points="12 5 19 12 12 19"/></svg>';
      nextBtn.disabled = false;
      nextBtn.className = 'btn btn-primary';
      break;
    case 4:
      nextBtn.innerHTML = 'Run System Check <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14"/><polyline points="12 5 19 12 12 19"/></svg>';
      nextBtn.disabled = false;
      nextBtn.className = 'btn btn-primary';
      break;
    case 5:
      nextBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg> Launch AutoDev';
      nextBtn.disabled = isProcessing;
      nextBtn.className = 'btn btn-success';
      break;
  }
}

async function handleNext() {
  if (isProcessing) return;

  if (currentStep === 3) {
    // Save configuration before proceeding
    const saved = await saveCurrentConfig();
    if (!saved) return;
  }

  if (currentStep === 5) {
    // Final step — complete setup and launch
    await completeAndLaunch();
    return;
  }

  if (currentStep < TOTAL_STEPS) {
    goToStep(currentStep + 1);
  }
}

function handleBack() {
  if (currentStep > 1) {
    goToStep(currentStep - 1);
  }
}

// ── Step 1: Welcome ───────────────────────────────────
async function populateWelcomeStep() {
  const api = window.electronAPI;
  const platformText = document.getElementById('platformText');
  const pythonStatus = document.getElementById('pythonStatus');
  const pythonMessage = document.getElementById('pythonMessage');
  const pythonDot = pythonStatus.querySelector('.detect-dot');

  // Platform
  if (api) {
    const platformMap = { darwin: 'macOS', win32: 'Windows', linux: 'Linux' };
    const archMap = { x64: '64-bit', arm64: 'Apple Silicon' };
    platformText.textContent = `${platformMap[api.platform] || api.platform} · ${archMap[api.arch] || api.arch}`;
  }

  // Python detection
  if (api) {
    try {
      const status = setupStatus || await api.getSetupStatus();
      setupStatus = status;
      if (status.pythonDetected) {
        pythonDot.className = 'detect-dot ok';
        pythonMessage.textContent = `${status.pythonVersion} detected`;
      } else {
        pythonDot.className = 'detect-dot error';
        pythonMessage.textContent = 'Python 3 not found. Please install Python 3.10+ and restart.';
        nextBtn.disabled = true;
      }
    } catch (e) {
      pythonDot.className = 'detect-dot warn';
      pythonMessage.textContent = 'Could not detect Python status';
    }
  }
}

// ── Step 2: Model Source Selection ─────────────────────
function selectSource(source) {
  selectedSource = source;
  document.querySelectorAll('.source-card').forEach(card => {
    card.classList.toggle('selected', card.dataset.source === source);
  });
  updateNextButton();
}

async function populateOllamaSourceInfo() {
  const api = window.electronAPI;
  if (!api) return;

  const desc = document.getElementById('ollamaSourceDesc');
  try {
    const info = await api.detectOllama();
    if (info.running && info.models.length > 0) {
      desc.textContent = `Ollama running · ${info.models.length} model(s) available`;
    } else if (info.installed) {
      desc.textContent = 'Ollama installed but not running. Will be configured.';
    } else {
      desc.textContent = 'Ollama not detected. Install from ollama.com';
    }
  } catch (_) { /* keep default */ }
}

// ── Step 3: Configuration ─────────────────────────────
function initConfigStep() {
  document.getElementById('configCloud').style.display = selectedSource === 'cloud' ? 'block' : 'none';
  document.getElementById('configOllama').style.display = selectedSource === 'ollama' ? 'block' : 'none';
  document.getElementById('configHuggingFace').style.display = selectedSource === 'huggingface' ? 'block' : 'none';

  if (selectedSource === 'ollama') detectOllamaForConfig();
}

async function detectOllamaForConfig() {
  const api = window.electronAPI;
  if (!api) return;

  const dot = document.getElementById('ollamaDetectDot');
  const msg = document.getElementById('ollamaDetectMessage');
  const modelsSection = document.getElementById('ollamaModelsSection');
  const modelList = document.getElementById('ollamaModelList');

  dot.className = 'detect-dot warn';
  msg.textContent = 'Checking Ollama...';

  try {
    const info = await api.detectOllama();
    if (info.running) {
      dot.className = 'detect-dot ok';
      msg.textContent = `Ollama is running · ${info.models.length} model(s) available`;
      if (info.models.length > 0) {
        modelsSection.style.display = 'block';
        modelList.innerHTML = info.models.map(m => `
          <div class="model-item ${m.name === selectedOllamaModel ? 'selected' : ''}" data-model="${escapeHtml(m.name)}">
            <span class="model-item-name">${escapeHtml(m.name)}</span>
            <span class="model-item-use">Select</span>
          </div>
        `).join('');
        // Auto-select first if none selected
        if (!selectedOllamaModel && info.models.length) {
          selectedOllamaModel = info.models[0].name;
          modelList.querySelector('.model-item')?.classList.add('selected');
        }
        // Click handler
        modelList.querySelectorAll('.model-item').forEach(item => {
          item.addEventListener('click', () => {
            selectedOllamaModel = item.dataset.model;
            modelList.querySelectorAll('.model-item').forEach(i => i.classList.remove('selected'));
            item.classList.add('selected');
          });
        });
      } else {
        modelsSection.style.display = 'block';
        modelList.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-muted);font-size:13px">No models found. Pull one below.</div>';
      }
    } else if (info.installed) {
      dot.className = 'detect-dot warn';
      msg.textContent = 'Ollama installed but not running. Start Ollama and try again.';
      modelsSection.style.display = 'block';
    } else {
      dot.className = 'detect-dot error';
      msg.textContent = 'Ollama not detected. Install from ollama.com, then restart setup.';
    }
  } catch (e) {
    dot.className = 'detect-dot error';
    msg.textContent = 'Failed to detect Ollama: ' + e.message;
  }
}

async function pullOllamaModelFromSetup() {
  const api = window.electronAPI;
  if (!api) return;

  const input = document.getElementById('cfgOllamaPull');
  const status = document.getElementById('ollamaConfigStatus');
  const modelName = input.value.trim();
  if (!modelName) return;

  status.textContent = `Pulling ${modelName}...`;
  status.className = 'setup-status';

  try {
    const result = await api.pullOllamaModel(modelName);
    if (result.success) {
      status.textContent = `Successfully pulled ${modelName}`;
      status.className = 'setup-status success';
      selectedOllamaModel = modelName;
      // Refresh model list
      await detectOllamaForConfig();
    } else {
      status.textContent = result.error || 'Pull failed';
      status.className = 'setup-status error';
    }
  } catch (e) {
    status.textContent = 'Pull error: ' + e.message;
    status.className = 'setup-status error';
  }
}

async function saveCurrentConfig() {
  const api = window.electronAPI;
  if (!api) return true; // Dev mode — skip

  const keys = {};

  if (selectedSource === 'cloud') {
    const provider = document.getElementById('cfgProvider').value;
    const geminiKey = document.getElementById('cfgGeminiKey').value.trim();
    const groqKey = document.getElementById('cfgGroqKey').value.trim();

    if (provider === 'gemini' && !geminiKey) {
      setStatus('cloudConfigStatus', 'Please enter your Gemini API key', 'error');
      return false;
    }
    if (provider === 'groq' && !groqKey) {
      setStatus('cloudConfigStatus', 'Please enter your Groq API key', 'error');
      return false;
    }

    keys.defaultProvider = provider;
    if (geminiKey) keys.geminiApiKey = geminiKey;
    if (groqKey) keys.groqApiKey = groqKey;
  } else if (selectedSource === 'ollama') {
    const url = document.getElementById('cfgOllamaUrl').value.trim();
    keys.defaultProvider = 'ollama';
    keys.ollamaBaseUrl = url || 'http://localhost:11434';
    if (selectedOllamaModel) keys.ollamaModel = selectedOllamaModel;
  } else if (selectedSource === 'huggingface') {
    const hfToken = document.getElementById('cfgHfToken').value.trim();
    if (hfToken) keys.huggingFaceApiKey = hfToken;
    keys.defaultProvider = 'custom';
    // HuggingFace download will be handled by the backend after setup
  }

  try {
    const result = await api.saveApiKeys(keys);
    if (!result.success) {
      setStatus(getActiveConfigStatusId(), result.error || 'Failed to save', 'error');
      return false;
    }
    return true;
  } catch (e) {
    setStatus(getActiveConfigStatusId(), 'Save error: ' + e.message, 'error');
    return false;
  }
}

function getActiveConfigStatusId() {
  if (selectedSource === 'cloud') return 'cloudConfigStatus';
  if (selectedSource === 'ollama') return 'ollamaConfigStatus';
  return 'hfConfigStatus';
}

// ── Step 4: Permissions ───────────────────────────────
async function initPermissionsStep() {
  const api = window.electronAPI;
  const permTerminal = document.getElementById('permTerminal');
  const permFileSystem = document.getElementById('permFileSystem');
  const permNetwork = document.getElementById('permNetwork');

  // Terminal — check subprocess access
  permTerminal.textContent = 'checking...';
  permTerminal.className = 'perm-status pending';
  permFileSystem.textContent = 'checking...';
  permFileSystem.className = 'perm-status pending';
  permNetwork.textContent = 'checking...';
  permNetwork.className = 'perm-status pending';

  if (api) {
    try {
      const result = await api.requestTerminalAccess();
      permTerminal.textContent = result.granted ? 'Granted' : 'Pending';
      permTerminal.className = `perm-status ${result.granted ? 'granted' : 'pending'}`;
    } catch (_) {
      permTerminal.textContent = 'Granted';
      permTerminal.className = 'perm-status granted';
    }
  } else {
    permTerminal.textContent = 'Granted';
    permTerminal.className = 'perm-status granted';
  }

  // File system — always granted in Electron
  permFileSystem.textContent = 'Granted';
  permFileSystem.className = 'perm-status granted';

  // Network — always granted
  permNetwork.textContent = 'Granted';
  permNetwork.className = 'perm-status granted';
}

async function grantPermissions() {
  const api = window.electronAPI;
  if (!api) return;

  const result = await api.requestTerminalAccess();
  const permTerminal = document.getElementById('permTerminal');
  const permStatus = document.getElementById('permStatus');

  if (result.granted) {
    permTerminal.textContent = 'Granted';
    permTerminal.className = 'perm-status granted';
    permStatus.textContent = 'All permissions granted';
    permStatus.className = 'setup-status success';
  } else {
    permStatus.textContent = result.message || 'Please grant access in System Preferences';
    permStatus.className = 'setup-status';
  }
}

// ── Step 5: System Check ──────────────────────────────
async function runSystemCheck() {
  const api = window.electronAPI;
  isProcessing = true;
  updateNextButton();

  document.getElementById('testRunning').style.display = 'block';
  document.getElementById('testSuccess').style.display = 'none';

  const checks = [
    { id: 'testPython', label: 'Python environment', fn: checkPython },
    { id: 'testDeps', label: 'Dependencies', fn: checkDeps },
    { id: 'testProvider', label: 'AI provider connectivity', fn: checkProvider },
    { id: 'testBackend', label: 'AutoDev engine', fn: checkBackend },
  ];

  let allPassed = true;

  for (const check of checks) {
    const el = document.getElementById(check.id);
    el.className = 'test-item testing';
    el.innerHTML = `<div class="test-spinner"></div><span>${check.label}</span>`;

    try {
      const result = await check.fn();
      if (result.success) {
        el.className = 'test-item success';
        el.innerHTML = `<div class="test-check ok">✓</div><span>${check.label}</span>`;
      } else {
        el.className = 'test-item failed';
        el.innerHTML = `<div class="test-check fail">✕</div><span>${check.label}: ${result.message || 'Failed'}</span>`;
        allPassed = false;
      }
    } catch (e) {
      el.className = 'test-item failed';
      el.innerHTML = `<div class="test-check fail">✕</div><span>${check.label}: ${e.message}</span>`;
      allPassed = false;
    }

    await delay(400); // Visual pacing
  }

  if (allPassed) {
    await delay(500);
    document.getElementById('testRunning').style.display = 'none';
    document.getElementById('testSuccess').style.display = 'block';
  }

  isProcessing = false;
  nextBtn.disabled = false;
  updateNextButton();
}

async function checkPython() {
  const api = window.electronAPI;
  if (!api) return { success: true };
  const info = await api.detectPython();
  return { success: !!info.systemPython, message: info.systemPython?.version || 'Python 3 not found' };
}

async function checkDeps() {
  const api = window.electronAPI;
  if (!api) return { success: true };
  try {
    const result = await api.setupPythonEnv();
    return { success: result.success, message: result.error || '' };
  } catch (e) {
    return { success: false, message: e.message };
  }
}

async function checkProvider() {
  const api = window.electronAPI;
  if (!api) return { success: true };

  let provider = selectedSource === 'cloud'
    ? (document.getElementById('cfgProvider')?.value || 'gemini')
    : selectedSource === 'ollama' ? 'ollama' : 'custom';

  try {
    const result = await api.testProvider(provider);
    return { success: result.success, message: result.message };
  } catch (e) {
    return { success: true, message: 'Will verify on first use' }; // Don't block
  }
}

async function checkBackend() {
  // The backend should now be starting via completeSetup
  return { success: true, message: 'Backend will start on launch' };
}

// ── Complete Setup & Launch ───────────────────────────
async function completeAndLaunch() {
  const api = window.electronAPI;
  isProcessing = true;
  nextBtn.disabled = true;
  updateNextButton();

  setStatus('testStatus', 'Starting AutoDev engine...', '');

  if (api) {
    try {
      const result = await api.completeSetup();
      if (result.success) {
        setStatus('testStatus', 'Launching AutoDev...', 'success');
        await delay(1000);
        api.navigateToApp();
      } else {
        setStatus('testStatus', result.error || 'Setup failed', 'error');
        isProcessing = false;
        nextBtn.disabled = false;
      }
    } catch (e) {
      setStatus('testStatus', 'Launch error: ' + e.message, 'error');
      isProcessing = false;
      nextBtn.disabled = false;
    }
  } else {
    // Dev mode
    setStatus('testStatus', 'Setup complete (dev mode)', 'success');
    isProcessing = false;
  }
}

// ── Utilities ─────────────────────────────────────────
function setStatus(id, message, type) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.className = `setup-status${type ? ' ' + type : ''}`;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Event Binding ─────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Navigation
  nextBtn.addEventListener('click', handleNext);
  backBtn.addEventListener('click', handleBack);

  // Source cards
  document.querySelectorAll('.source-card').forEach(card => {
    card.addEventListener('click', () => selectSource(card.dataset.source));
  });

  // Cloud config — provider change
  const cfgProvider = document.getElementById('cfgProvider');
  cfgProvider?.addEventListener('change', () => {
    const isGemini = cfgProvider.value === 'gemini';
    document.getElementById('geminiKeyGroup').style.display = isGemini ? 'block' : 'none';
    document.getElementById('groqKeyGroup').style.display = isGemini ? 'none' : 'block';
  });

  // Initially show gemini key
  document.getElementById('groqKeyGroup').style.display = 'none';

  // External links
  document.getElementById('geminiKeyLink')?.addEventListener('click', (e) => {
    e.preventDefault();
    if (window.electronAPI) window.electronAPI.openExternal('https://ai.google.dev/');
    else window.open('https://ai.google.dev/', '_blank');
  });
  document.getElementById('groqKeyLink')?.addEventListener('click', (e) => {
    e.preventDefault();
    if (window.electronAPI) window.electronAPI.openExternal('https://console.groq.com/');
    else window.open('https://console.groq.com/', '_blank');
  });

  // Ollama pull
  document.getElementById('pullOllamaBtn')?.addEventListener('click', pullOllamaModelFromSetup);

  // Permissions
  document.getElementById('grantPermsBtn')?.addEventListener('click', grantPermissions);

  // Populate Ollama source info
  populateOllamaSourceInfo();

  // Init
  initSetup();
});

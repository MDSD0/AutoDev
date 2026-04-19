/* =====================================================
   AutoDev v4 — app.js
   Handles all pipeline nodes, collapsible cards,
   quality scoring, guidance banner, conversation mode,
   and session resume.
   ===================================================== */

// ── State ─────────────────────────────────────────────────
let sessionId = newSessionId();
let currentFiles = {};   // {filename: content}
let currentSpec = null;
let isRunning = false;
let currentSavedFiles = [];
let abortController = null;
let currentMode = "auto"; // "auto" | "plan" | "fast"
let currentPlan = null;
let activeCodeFile = null;
function newSessionId() {
    return "ses_" + Math.random().toString(36).slice(2, 11);
}

// ── DOM Refs ──────────────────────────────────────────────
const chatFeed = document.getElementById("chatFeed");
const welcomeScreen = document.getElementById("welcomeScreen");
const taskInput = document.getElementById("taskInput");
const runBtn = document.getElementById("runBtn");
const stopBtn = document.getElementById("stopBtn");
const refineBtn = document.getElementById("refineBtn");
const sessionList = document.getElementById("sessionList");
const newProjectBtn = document.getElementById("newProjectBtn");
const sidebarToggle = document.getElementById("sidebarToggle");
const sidebar = document.getElementById("sidebar");
const wipeRagBtn = document.getElementById("wipeRagBtn");
const launchBtn = document.getElementById("launchBtn");
const launchFileBtn = document.getElementById("launchFileBtn");
const rerunBtn = document.getElementById("rerunBtn");
const copyCodeBtn = document.getElementById("copyCodeBtn");
const execOutput = document.getElementById("execOutput");
const codeContent = document.getElementById("codeContent");
const codeLang = document.getElementById("codeLang");
const codeFileTabs = document.getElementById("codeFileTabs");
const rightPanel = document.getElementById("rightPanel");
const expandPanelBtn = document.getElementById("expandPanelBtn");
const collapsePanelBtn = document.getElementById("collapsePanelBtn");
const filesPath = document.getElementById("filesPath");
const filesList = document.getElementById("filesList");
const specPanel = document.getElementById("specPanel");
const retriesInput = document.getElementById("retriesInput");
const retriesLabel = document.getElementById("retriesLabel");
const modelSelect = document.getElementById("modelSelect");
const modelIcon = document.getElementById("modelIcon");
const refineToggle = document.getElementById("refineToggle");
const guidanceBanner = document.getElementById("guidanceBanner");
const guidanceInput = document.getElementById("guidanceInput");
const continueBtn = document.getElementById("continueBtn");
const themeToggle = document.getElementById("themeToggle");
const themeToggleIcon = document.getElementById("themeToggleIcon");
const setupPrompt = document.getElementById("setupPrompt");
const setupProvider = document.getElementById("setupProvider");
const setupApiKey = document.getElementById("setupApiKey");
const setupSaveBtn = document.getElementById("setupSaveBtn");
const setupDismissBtn = document.getElementById("setupDismissBtn");
const setupOpenSettingsBtn = document.getElementById("setupOpenSettingsBtn");
const setupBridgeBtn = document.getElementById("setupBridgeBtn");
const setupPromptStatus = document.getElementById("setupPromptStatus");

const THEME_STORAGE_KEY = "autodev-theme";
const UI_STATE_STORAGE_KEY = "autodev-ui-state-v1";
const API_BASE_STORAGE_KEY = "autodev-api-base-v1";
const USER_SCOPE_STORAGE_KEY = "autodev-user-scope-v1";
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";
const settingsStore = window.AutoDevSettings || null;

function getOrCreateUserScope() {
    try {
        const existing = localStorage.getItem(USER_SCOPE_STORAGE_KEY);
        if (existing) return existing;
        const created = "u_" + Math.random().toString(36).slice(2, 14);
        localStorage.setItem(USER_SCOPE_STORAGE_KEY, created);
        return created;
    } catch (e) {
        console.warn("[user-scope]", e);
        return "u_browser";
    }
}

function normalizeApiBase(value) {
    return (value || "").trim().replace(/\/+$/, "");
}

function getStoredApiBase() {
    try {
        return normalizeApiBase(localStorage.getItem(API_BASE_STORAGE_KEY) || "");
    } catch (e) {
        console.warn("[api-base]", e);
        return "";
    }
}

function defaultApiBase() {
    // Desktop mode: always use the local backend
    if (window.electronAPI && window.electronAPI.isDesktop) {
        return "http://127.0.0.1:8000";
    }
    return "";
}

function getApiBase() {
    return getStoredApiBase() || defaultApiBase();
}

function setApiBase(nextBase) {
    try {
        const normalized = normalizeApiBase(nextBase);
        if (normalized) localStorage.setItem(API_BASE_STORAGE_KEY, normalized);
        else localStorage.removeItem(API_BASE_STORAGE_KEY);
    } catch (e) {
        console.warn("[api-base]", e);
    }
}

function apiUrl(url) {
    if (!url) return getApiBase() || "";
    if (/^https?:\/\//i.test(url)) return url;
    const base = getApiBase();
    return base ? `${base}${url}` : url;
}

function apiFetch(url, options = {}) {
    const headers = settingsStore
        ? settingsStore.buildRuntimeHeaders(options.headers || {})
        : (options.headers || {});
    headers["X-Autodev-User"] = getOrCreateUserScope();
    return fetch(apiUrl(url), { ...options, headers });
}

function mergeServerSettingsIntoLocal(serverSettings) {
    if (!settingsStore || !serverSettings) return;
    settingsStore.mergeServerSettings(serverSettings, settingsStore.loadSettings());
}

function isRuntimeConfigured() {
    // Desktop mode: always configured (setup wizard handles it)
    if (window.electronAPI && window.electronAPI.isDesktop) return true;
    if (!settingsStore) return true;
    if (getStoredApiBase()) return true;
    return settingsStore.hasConfiguredRuntime(settingsStore.loadSettings());
}

function syncSetupPromptFields() {
    if (!settingsStore || !setupProvider || !setupApiKey) return;
    const settings = settingsStore.loadSettings();
    const provider = settings.providerRegistry?.defaultProvider || settings.workflowDefaults?.defaultProvider || "groq";
    setupProvider.value = ["gemini", "groq"].includes(provider) ? provider : "groq";
    setupApiKey.value =
        provider === "gemini"
            ? (settings.secrets?.geminiApiKey || "")
            : (settings.secrets?.groqApiKey || "");
}

function openSetupPrompt() {
    if (!setupPrompt) return;
    syncSetupPromptFields();
    if (setupPromptStatus) {
        const bridgeBase = getApiBase() || DEFAULT_BRIDGE_URL;
        setupPromptStatus.textContent = bridgeBase
            ? `Local bridge target: ${bridgeBase}`
            : "Using the same server for API requests.";
    }
    setupPrompt.hidden = false;
}

function closeSetupPrompt() {
    if (!setupPrompt) return;
    setupPrompt.hidden = true;
}

function handleQuickProviderChange() {
    if (!settingsStore || !setupProvider || !setupApiKey) return;
    const settings = settingsStore.loadSettings();
    setupApiKey.value =
        setupProvider.value === "gemini"
            ? (settings.secrets?.geminiApiKey || "")
            : (settings.secrets?.groqApiKey || "");
}

function saveQuickSetup() {
    if (!settingsStore || !setupProvider || !setupApiKey) return;
    const provider = setupProvider.value;
    const apiKey = setupApiKey.value.trim();
    if (!apiKey) {
        setupApiKey.focus();
        return;
    }
    settingsStore.updateSettings((draft) => {
        draft.workflowDefaults.defaultProvider = provider;
        draft.providerRegistry.defaultProvider = provider;
        if (provider === "gemini") {
            draft.secrets.geminiApiKey = apiKey;
        } else if (provider === "groq") {
            draft.secrets.groqApiKey = apiKey;
        }
        return draft;
    });
    closeSetupPrompt();
    applyStoredRunnerDefaults();
}

async function connectLocalBridge() {
    setApiBase(DEFAULT_BRIDGE_URL);
    if (setupPromptStatus) {
        setupPromptStatus.textContent = `Checking ${DEFAULT_BRIDGE_URL}…`;
    }
    try {
        const res = await apiFetch("/status");
        const data = await res.json();
        if (data?.status === "running") {
            if (setupPromptStatus) {
                setupPromptStatus.textContent = `Connected to local bridge at ${DEFAULT_BRIDGE_URL}`;
            }
            closeSetupPrompt();
            await syncRunnerDefaultsFromServer();
            await loadSessions();
            return;
        }
        if (setupPromptStatus) {
            setupPromptStatus.textContent = "Bridge responded, but it did not look like AutoDev.";
        }
    } catch (e) {
        if (setupPromptStatus) {
            setupPromptStatus.textContent = "Bridge not reachable. Run `python3 run_bridge.py` locally, then try again.";
        }
    }
}

function ensureRuntimeConfigured() {
    if (isRuntimeConfigured()) return true;
    openSetupPrompt();
    return false;
}

function readUiState() {
    try {
        return JSON.parse(localStorage.getItem(UI_STATE_STORAGE_KEY) || "{}");
    } catch (e) {
        console.warn("[ui-state]", e);
        return {};
    }
}

function writeUiState(nextState) {
    try {
        localStorage.setItem(UI_STATE_STORAGE_KEY, JSON.stringify(nextState));
    } catch (e) {
        console.warn("[ui-state]", e);
    }
}

function getSessionUiState(targetSessionId = sessionId) {
    const ui = readUiState();
    return (ui.sessions && ui.sessions[targetSessionId]) || {};
}

function updateSessionUiState(patch, targetSessionId = sessionId) {
    if (!targetSessionId) return;
    const ui = readUiState();
    ui.sessions = ui.sessions || {};
    ui.sessions[targetSessionId] = {
        ...(ui.sessions[targetSessionId] || {}),
        ...patch,
        updatedAt: Date.now(),
    };
    writeUiState(ui);
}

// ── Init ──────────────────────────────────────────────────
async function init() {
    applyTheme(getPreferredTheme());
    lucide.createIcons();
    await syncRunnerDefaultsFromServer();
    await loadSessions();

    document.addEventListener("click", (e) => {
        if (!e.target.closest('.kebab-btn')) {
            document.querySelectorAll(".session-dropdown.show").forEach(d => d.classList.remove("show"));
        }
    });

    if (collapsePanelBtn) {
        collapsePanelBtn.addEventListener("click", () => {
            rightPanel.classList.add("minimized");
            expandPanelBtn.style.display = "inline-flex";
        });
    }
    if (expandPanelBtn) {
        expandPanelBtn.addEventListener("click", () => {
            rightPanel.classList.remove("minimized");
            expandPanelBtn.style.display = "none";
        });
    }

    stopBtn.addEventListener("click", () => {
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
    });

    taskInput.addEventListener("keydown", e => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (!isRunning) runTask();
        }
    });
    taskInput.addEventListener("input", () => autoResize(taskInput));
    guidanceInput.addEventListener("input", () => autoResize(guidanceInput));

    modelSelect.addEventListener("change", updateModelIcon);
    setupProvider?.addEventListener("change", handleQuickProviderChange);
    setupSaveBtn?.addEventListener("click", saveQuickSetup);
    setupDismissBtn?.addEventListener("click", closeSetupPrompt);
    setupOpenSettingsBtn?.addEventListener("click", () => { window.location.href = "/settings"; });
    setupBridgeBtn?.addEventListener("click", connectLocalBridge);

    if (themeToggle) {
        themeToggle.addEventListener("click", toggleTheme);
    }

    runBtn.addEventListener("click", () => { if (!isRunning) runTask(); });
    refineBtn.addEventListener("click", refinePrompt);
    newProjectBtn.addEventListener("click", startNewProject);
    sidebarToggle.addEventListener("click", () => sidebar.classList.toggle("collapsed"));
    wipeRagBtn.addEventListener("click", wipeRag);
    launchBtn.addEventListener("click", () => launchSession());
    launchFileBtn.addEventListener("click", () => launchSession());
    rerunBtn.addEventListener("click", rerunFile);
    copyCodeBtn.addEventListener("click", copyCode);
    continueBtn.addEventListener("click", continueWithGuidance);
    retriesInput.addEventListener("input", () => retriesLabel.textContent = retriesInput.value);

    // Panel tab switching
    document.querySelectorAll(".ptab").forEach(tab => {
        tab.addEventListener("click", () => switchTab(tab.dataset.tab));
    });

    // Mode selector
    document.querySelectorAll(".mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            currentMode = btn.dataset.mode;
            document.querySelectorAll(".mode-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            // Animate indicator
            updateModeIndicator();
        });
    });
    applyStoredRunnerDefaults();
    // Initialize mode indicator position
    requestAnimationFrame(updateModeIndicator);

    // Welcome chips
    document.querySelectorAll(".chip").forEach(chip => {
        chip.addEventListener("click", () => {
            taskInput.value = chip.dataset.task;
            autoResize(taskInput);
            taskInput.focus();
        });
    });

    if (!isRuntimeConfigured()) {
        openSetupPrompt();
    }

    // Desktop mode: log it in console
    if (window.electronAPI && window.electronAPI.isDesktop) {
        console.log('[AutoDev] Running in desktop mode on', window.electronAPI.platform);
    }
}

async function syncRunnerDefaultsFromServer() {
    if (!settingsStore) return;
    try {
        const res = await apiFetch("/settings/runtime");
        const data = await res.json();
        mergeServerSettingsIntoLocal(data);
    } catch (e) {
        console.warn("[runner-defaults-server]", e);
    }
}

function updateModelIcon() {
    const mapping = { "auto": "layers", "groq": "zap", "gemini": "sparkles", "ollama": "box", "custom": "plug" };
    modelIcon.setAttribute("data-lucide", mapping[modelSelect.value] || "cpu");
    lucide.createIcons();
}

function applyStoredRunnerDefaults() {
    if (!settingsStore) {
        updateModelIcon();
        return;
    }
    try {
        const defaults = settingsStore.getRunnerDefaults();
        const allowedModes = new Set(["auto", "plan", "fast"]);
        const providerOptionExists = Array.from(modelSelect.options).some(option => option.value === defaults.provider);

        modelSelect.value = providerOptionExists ? defaults.provider : "auto";
        refineToggle.checked = defaults.refinePrompt !== false;
        retriesInput.value = String(defaults.maxRetries || 6);
        retriesLabel.textContent = retriesInput.value;

        if (allowedModes.has(defaults.mode)) {
            currentMode = defaults.mode;
            document.querySelectorAll(".mode-btn").forEach(btn => {
                btn.classList.toggle("active", btn.dataset.mode === currentMode);
            });
        }
    } catch (e) {
        console.warn("[runner-defaults]", e);
    }
    updateModelIcon();
}

function appendRuntimeLog(type, message, details = {}) {
    if (!settingsStore) return;
    try {
        settingsStore.appendLog({
            type,
            message,
            details,
        });
    } catch (e) {
        console.warn("[runtime-log]", e);
    }
}

function updateModeIndicator() {
    const activeBtn = document.querySelector(`.mode-btn[data-mode="${currentMode}"]`);
    const indicator = document.getElementById("modeIndicator");
    const container = document.getElementById("modeSelector");
    if (activeBtn && indicator && container) {
        const containerRect = container.getBoundingClientRect();
        const btnRect = activeBtn.getBoundingClientRect();
        indicator.style.width = btnRect.width + "px";
        indicator.style.transform = `translateX(${btnRect.left - containerRect.left - 2}px)`;
    }
}

function getPreferredTheme() {
    try {
        const savedTheme = localStorage.getItem(THEME_STORAGE_KEY);
        if (savedTheme === "light" || savedTheme === "dark") return savedTheme;
    } catch (e) {
        console.warn("[theme]", e);
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    if (themeToggle) {
        const nextTheme = theme === "light" ? "dark" : "light";
        themeToggle.setAttribute("title", `Switch to ${nextTheme} theme`);
        themeToggle.setAttribute("aria-label", `Switch to ${nextTheme} theme`);
        themeToggle.setAttribute("aria-pressed", String(theme === "light"));
    }
    if (themeToggleIcon) {
        themeToggleIcon.setAttribute("data-lucide", theme === "light" ? "moon-star" : "sun-medium");
    }
    lucide.createIcons();
}

function toggleTheme() {
    const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(nextTheme);
    try {
        localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch (e) {
        console.warn("[theme]", e);
    }
}

function switchTab(name, persist = true) {
    document.querySelectorAll(".ptab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll(".panel-content").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
    if (persist) updateSessionUiState({ activePanelTab: name });
}

function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

function setExecOutput(text = "", success = null) {
    if (!text) {
        execOutput.innerHTML = `<span class="term-placeholder">Execution output will appear here…</span>`;
        execOutput.className = "terminal";
        return;
    }
    execOutput.textContent = text;
    execOutput.className = "terminal";
    if (success === true) execOutput.classList.add("txt-green");
    if (success === false) execOutput.classList.add("txt-red");
}

function hasProjectSnapshot() {
    const outputText = execOutput ? execOutput.textContent.trim() : "";
    return Boolean(
        currentSpec ||
        currentPlan ||
        Object.keys(currentFiles || {}).length ||
        currentSavedFiles.length ||
        (outputText && outputText !== "Execution output will appear here…")
    );
}

function updateFileListSelection(filename) {
    filesList.querySelectorAll(".file-item").forEach(item => {
        item.classList.toggle("active", item.dataset.filename === filename);
    });
}

// ── Sessions ──────────────────────────────────────────────

function relativeTime(unixSeconds) {
    if (!unixSeconds) return "";
    const now = Date.now() / 1000;
    const diff = now - unixSeconds;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    if (diff < 604800) return Math.floor(diff / 86400) + "d ago";
    return new Date(unixSeconds * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function outputTypeIcon(type) {
    const map = {
        "python": "file-code",
        "html": "globe",
        "streamlit": "layout",
        "c": "terminal",
        "cpp": "terminal",
        "java": "coffee",
        "go": "terminal",
        "rust": "terminal",
        "shell": "terminal",
        "javascript": "braces",
    };
    return map[(type || "").toLowerCase()] || "";
}

function outputTypeBadge(type) {
    if (!type) return "";
    const labels = {
        "python": "PY",
        "html": "HTML",
        "streamlit": "ST",
        "c": "C",
        "cpp": "C++",
        "java": "Java",
        "go": "Go",
        "rust": "Rust",
        "shell": "SH",
        "javascript": "JS",
    };
    return labels[(type || "").toLowerCase()] || type.toUpperCase().slice(0, 3);
}

async function loadSessions() {
    try {
        const res = await apiFetch("/sessions");
        const sessions = await res.json();
        sessionList.innerHTML = "";

        // Group sessions: Today, Yesterday, This Week, Older
        const now = Date.now() / 1000;
        const groups = { today: [], yesterday: [], week: [], older: [] };
        sessions.forEach(s => {
            const age = now - (s.updated_at || 0);
            if (age < 86400) groups.today.push(s);
            else if (age < 172800) groups.yesterday.push(s);
            else if (age < 604800) groups.week.push(s);
            else groups.older.push(s);
        });

        const renderGroup = (label, items) => {
            if (items.length === 0) return;
            const header = document.createElement("div");
            header.className = "session-group-header";
            header.textContent = label;
            sessionList.appendChild(header);
            items.forEach(s => renderSessionItem(s));
        };

        renderGroup("Today", groups.today);
        renderGroup("Yesterday", groups.yesterday);
        renderGroup("This Week", groups.week);
        renderGroup("Older", groups.older);

        lucide.createIcons();
    } catch (e) { console.warn("[sessions]", e); }
}

function renderSessionItem(s) {
    const btn = document.createElement("div");
    btn.className = "session-item" + (s.id === sessionId ? " active" : "");
    btn.dataset.id = s.id;

    // Left side: status dot + content
    const leftWrap = document.createElement("div");
    leftWrap.className = "session-item-left";

    const dot = document.createElement("span");
    dot.className = "session-status " + (s.status || "");
    leftWrap.appendChild(dot);

    const contentWrap = document.createElement("div");
    contentWrap.className = "session-item-content-wrap";

    const title = document.createElement("span");
    title.className = "session-item-title";
    // Use the smart title — no fallback to ID needed since backend handles it
    title.textContent = s.title || s.id;
    contentWrap.appendChild(title);

    // Subtitle: output type badge + relative time
    const subtitle = document.createElement("div");
    subtitle.className = "session-item-meta";

    if (s.output_type) {
        const badge = document.createElement("span");
        badge.className = "session-type-badge " + (s.output_type || "").toLowerCase();
        badge.textContent = outputTypeBadge(s.output_type);
        subtitle.appendChild(badge);
    }

    if (s.updated_at) {
        const time = document.createElement("span");
        time.className = "session-item-time";
        time.textContent = relativeTime(s.updated_at);
        subtitle.appendChild(time);
    }

    contentWrap.appendChild(subtitle);
    leftWrap.appendChild(contentWrap);
    btn.appendChild(leftWrap);

    btn.title = s.title || s.id;
    btn.addEventListener("click", () => loadSession(s.id));

    // Kebab menu
    const actWrap = document.createElement("div");
    actWrap.style.position = "relative";

    const kebabBtn = document.createElement("button");
    kebabBtn.className = "kebab-btn";
    kebabBtn.innerHTML = '<i data-lucide="more-vertical" style="width:14px;height:14px;"></i>';
    actWrap.appendChild(kebabBtn);

    const dropdown = document.createElement("div");
    dropdown.className = "session-dropdown";

    const renameItem = document.createElement("button");
    renameItem.className = "dropdown-item";
    renameItem.innerHTML = '<i data-lucide="edit-2"></i> Rename';
    renameItem.addEventListener("click", async (e) => {
        e.stopPropagation();
        dropdown.classList.remove("show");
        const newTitle = prompt("Enter new session name:", title.textContent);
        if (newTitle && newTitle.trim()) {
            await apiFetch(`/sessions/${s.id}/rename`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: newTitle.trim() })
            });
            loadSessions();
        }
    });

    const delItem = document.createElement("button");
    delItem.className = "dropdown-item";
    delItem.style.color = "var(--red)";
    delItem.innerHTML = '<i data-lucide="trash-2"></i> Delete';
    delItem.addEventListener("click", async (e) => {
        e.stopPropagation();
        dropdown.classList.remove("show");
        if (confirm("Delete this session?")) {
            await apiFetch(`/sessions/${s.id}`, { method: "DELETE" });
            if (s.id === sessionId) clearChat(true);
            loadSessions();
        }
    });

    dropdown.appendChild(renameItem);
    dropdown.appendChild(delItem);
    actWrap.appendChild(dropdown);

    kebabBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        document.querySelectorAll(".session-dropdown.show").forEach(d => {
            if (d !== dropdown) d.classList.remove("show");
        });
        dropdown.classList.toggle("show");
    });

    btn.appendChild(actWrap);
    sessionList.appendChild(btn);
}

async function loadSession(id) {
    sessionId = id;
    currentFiles = {};
    currentSpec = null;
    currentPlan = null;
    currentSavedFiles = [];
    activeCodeFile = null;
    guidanceBanner.style.display = "none";

    document.querySelectorAll(".session-item").forEach(el => {
        el.classList.toggle("active", el.dataset.id === id);
    });

    try {
        const [histResult, stateResult, filesResult] = await Promise.allSettled([
            apiFetch(`/sessions/${id}`),
            apiFetch(`/sessions/${id}/state`),
            apiFetch(`/session_files/${id}`),
        ]);

        clearChat(false);
        let loadedState = null;
        let loadedFiles = [];

        if (histResult.status === "fulfilled" && histResult.value.ok) {
            const history = await histResult.value.json();
            history.forEach(msg => renderHistoryMsg(msg));
        }

        if (stateResult.status === "fulfilled" && stateResult.value.ok) {
            loadedState = await stateResult.value.json();
            currentSpec = loadedState.spec || null;
            currentPlan = loadedState.plan || null;
            renderSpecPanel(currentSpec, currentPlan);

            if (loadedState.status === "awaiting_user") {
                guidanceBanner.style.display = "block";
            }

            const execText = loadedState.exec_output || loadedState.exec_report?.output || "";
            setExecOutput(execText, execText ? Boolean(loadedState.exec_success) : null);
            OutputConsole.setTerminalOutput(execText);
        } else {
            renderSpecPanel(null, null);
            setExecOutput("");
            OutputConsole.setTerminalOutput("");
        }

        if (filesResult.status === "fulfilled" && filesResult.value.ok) {
            const data = await filesResult.value.json();
            loadedFiles = data.files || [];
            renderFilesList(loadedFiles, data.dir);
            if (loadedFiles.length > 0) {
                currentFiles = {};
                loadedFiles.forEach(f => { currentFiles[f.name] = f.content; });
                renderCodePanel(currentFiles, getSessionUiState(id).activeCodeFile);
            } else {
                renderCodePanel({});
            }
        } else {
            renderFilesList([], "");
            renderCodePanel({});
        }

        const shouldShowPanel = Boolean(loadedFiles.length || currentSpec || currentPlan || (loadedState && (loadedState.exec_output || loadedState.exec_report?.output)));
        rightPanel.style.display = shouldShowPanel ? "flex" : "none";
        rerunBtn.style.display = loadedFiles.length ? "inline-flex" : "none";
        launchBtn.style.display = loadedFiles.length ? "inline-flex" : "none";
        launchFileBtn.style.display = loadedFiles.length ? "inline-flex" : "none";

        const nextTab = getSessionUiState(id).activePanelTab || (loadedFiles.length ? "code" : "output");
        switchTab(nextTab, false);

        chatFeed.scrollTop = 0;

        // Refresh output console for the loaded session
        OutputConsole.refresh(id);
    } catch (e) { console.warn("[load session]", e); }
}

function renderHistoryMsg(msg) {
    if (msg.name === "User") {
        appendUserBubble(msg.content);
        return;
    }
    const row = document.createElement("div");
    row.className = "ai-row";
    row.innerHTML = `
        <div class="ai-avatar"><i data-lucide="zap"></i></div>
        <div class="ai-body">
            <div class="thought-card">
                <div class="thought-header">
                    <span class="label"><i data-lucide="cpu"></i> ${esc(msg.name || "AI")}</span>
                </div>
                <div class="thought-body">${esc(stripCodeFences(msg.content))}</div>
                </div>
            </div>`;
    insertChatItem(row);
    lucide.createIcons();
}

function stripCodeFences(text) {
    return text.replace(/```[\w]*\n?/g, "").replace(/```$/g, "").trim();
}

// ── Chat Helpers ──────────────────────────────────────────
function clearChat(hideWelcome = true) {
    Array.from(chatFeed.children).forEach(child => {
        if (child.id !== "welcomeScreen") chatFeed.removeChild(child);
    });
    welcomeScreen.style.display = hideWelcome ? "none" : "";
}

function insertChatItem(node, { hideWelcome = true, scroll = true } = {}) {
    if (hideWelcome) welcomeScreen.style.display = "none";
    chatFeed.prepend(node);
    if (scroll) scrollChat();
}

function appendUserBubble(text) {
    const div = document.createElement("div");
    div.className = "user-bubble";
    div.textContent = text;
    insertChatItem(div);
}

function appendAiRow(bodyHtml) {
    const row = document.createElement("div");
    row.className = "ai-row";
    row.innerHTML = `<div class="ai-avatar"><i data-lucide="zap"></i></div><div class="ai-body">${bodyHtml}</div>`;
    insertChatItem(row);
    try { lucide.createIcons(); } catch (e) { }
    return row;
}

function scrollChat() {
    requestAnimationFrame(() => {
        chatFeed.scrollTop = 0;
    });
}

// ── Thought Card Factory ──────────────────────────────────
function makeCard(icon, label, bodyClass, collapsible = true) {
    const card = document.createElement("div");
    card.className = "thought-card" + (collapsible ? " collapsible" : "");
    card.innerHTML = `
        <div class="thought-header">
            <span class="label"><i data-lucide="${icon}"></i> ${esc(label)}</span>
            ${collapsible ? '<i data-lucide="chevron-down" class="thought-collapse-icon"></i>' : ""}
        </div>
        <div class="thought-body${bodyClass ? " " + bodyClass : ""}"></div>`;
    if (collapsible) {
        card.querySelector(".thought-header").addEventListener("click", () => {
            card.classList.toggle("collapsed");
            try { lucide.createIcons(); } catch (e) { }
        });
    }
    try { lucide.createIcons(); } catch (e) { }
    return card;
}

function addCard(row, card) {
    const area = row.querySelector(".thought-area");
    if (area) {
        area.appendChild(card);
        try { lucide.createIcons(); } catch (e) { }
        scrollChat();
    }
}

// ── Pipeline Block ────────────────────────────────────────
function createPipelineBlock() {
    const tpl = document.getElementById("pipelineTpl");
    const clone = tpl.content.cloneNode(true);
    const row = document.createElement("div");
    row.className = "ai-row";

    row.innerHTML = `<div class="ai-avatar"><i data-lucide="zap"></i></div><div class="ai-body"></div>`;
    const body = row.querySelector(".ai-body");

    const hud = document.createElement("div");
    hud.className = "workflow-hud";
    hud.innerHTML = `
        <div class="workflow-hud-left">
            <span class="workflow-hud-label">Workflow</span>
            <span class="workflow-hud-state">Starting…</span>
        </div>
        <div class="workflow-hud-track">
            <div class="workflow-hud-fill"></div>
        </div>
    `;

    body.appendChild(hud);
    body.appendChild(clone);

    insertChatItem(row, { scroll: false });
    try { lucide.createIcons(); } catch (e) { }
    scrollChat();
    return row;
}

function markNode(row, nodeName, state) {
    const n = row.querySelector(`[data-node="${nodeName}"]`);
    if (!n) return;

    n.classList.remove("active", "done", "error");
    if (state) n.classList.add(state);

    const i = n.querySelector("i");
    if (i) {
        if (state === "active") {
            if (!i.hasAttribute("data-original-lucide")) {
                i.setAttribute("data-original-lucide", i.getAttribute("data-lucide"));
            }
            i.setAttribute("data-lucide", "loader-2");
            i.classList.add("spinning");
        } else {
            i.classList.remove("spinning");
            if (i.hasAttribute("data-original-lucide")) {
                i.setAttribute("data-lucide", state === "done" ? "check-circle-2" : i.getAttribute("data-original-lucide"));
            }
        }
        try { lucide.createIcons(); } catch (e) {}
    }

    if (state === "active") {
        updateWorkflowHud(row, "running", `${workflowLabel(nodeName)}…`);
    } else if (state === "done") {
        updateWorkflowHud(row, "running", `${workflowLabel(nodeName)} done`);
    } else if (state === "error") {
        updateWorkflowHud(row, "error", `${workflowLabel(nodeName)} error`);
    }
}

const NODE_SEQUENCE = [
    "prompt_refiner", "spec_agent", "rag_retriever",
    "planner_agent", "dependency_installer",
    "implementer_agent", "executor", "error_classifier", "reviewer_agent"
];
const NODE_NEXT = {};
NODE_SEQUENCE.forEach((n, i) => { if (i < NODE_SEQUENCE.length - 1) NODE_NEXT[n] = NODE_SEQUENCE[i + 1]; });
// rag_retriever has no pipeline bar node — skip it
NODE_NEXT["spec_agent"] = "planner_agent";

// ── Main Run ──────────────────────────────────────────────
async function runTask() {
    const task = taskInput.value.trim();
    if (!task) return;
    if (!ensureRuntimeConfigured()) return;

    setRunning(true);
    taskInput.value = "";
    autoResize(taskInput);

    appendUserBubble(task);
    currentFiles = {};
    currentSpec = null;
    currentPlan = null;
    currentSavedFiles = [];
    activeCodeFile = null;
    guidanceBanner.style.display = "none";
    launchBtn.style.display = "none";
    rerunBtn.style.display = "none";
    launchFileBtn.style.display = "none";
    rightPanel.style.display = "flex";
    OutputConsole.close();
    switchTab("output", false);
    execOutput.innerHTML = `<span class="term-placeholder">Pipeline running…</span>`;
    execOutput.className = "terminal";
    OutputConsole.showFab();
    codeContent.textContent = "";
    codeLang.textContent = "—";
    codeFileTabs.innerHTML = "";
    specPanel.innerHTML = `<div class="spec-empty">Extracting spec…</div>`;

    let pipelineRow = createPipelineBlock();
    markNode(pipelineRow, "prompt_refiner", "active");

    const payload = {
        task,
        session_id: sessionId,
        model: modelSelect.value,
        refine_prompt: refineToggle.checked,
        max_retries: parseInt(retriesInput.value),
        mode: currentMode,
    };
    appendRuntimeLog(
        "run_task",
        `Started task with provider=${payload.model}, mode=${payload.mode}, retries=${payload.max_retries}, refine=${payload.refine_prompt}`,
        payload
    );

    try {
        abortController = new AbortController();
        const res = await apiFetch("/stream_task", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: abortController.signal
        });
        if (!res.body) throw new Error("No readable stream");
        await consumeSSE(res, pipelineRow);
    } catch (e) {
        if (e.name === "AbortError") {
            const card = makeCard("info", "Generation Stopped", "muted-color");
            card.querySelector(".thought-body").textContent = "Generation was manually stopped.";
            addCard(pipelineRow, card);
        } else {
            console.error("[stream]", e);
            const card = makeCard("alert-circle", "Stream Error", "error-color");
            card.querySelector(".thought-body").textContent = String(e);
            addCard(pipelineRow, card);
        }
    } finally {
        abortController = null;
        if (pipelineRow) settleWorkflow(pipelineRow, "complete");
        setRunning(false);
        await loadSessions();
        loadSessionFiles(sessionId);
    }
}

async function continueWithGuidance() {
    const guidance = guidanceInput.value.trim();
    if (!guidance || isRunning) return;
    if (!ensureRuntimeConfigured()) return;

    setRunning(true);
    guidanceBanner.style.display = "none";
    guidanceInput.value = "";

    appendUserBubble("Guidance: " + guidance);

    const pipelineRow = createPipelineBlock();
    markNode(pipelineRow, "implementer_agent", "active");

    const payload = {
        session_id: sessionId,
        user_guidance: guidance,
        model: modelSelect.value,
        max_retries: 3,
    };
    appendRuntimeLog(
        "continue_task",
        `Continued task with provider=${payload.model} and guidance.`,
        payload
    );

    try {
        abortController = new AbortController();
        const res = await apiFetch("/continue_task", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: abortController.signal
        });
        if (!res.body) throw new Error("No readable stream");
        await consumeSSE(res, pipelineRow);
    } catch (e) {
        if (e.name === "AbortError") {
            const card = makeCard("info", "Generation Stopped", "muted-color");
            card.querySelector(".thought-body").textContent = "Generation was manually stopped.";
            addCard(pipelineRow, card);
        } else {
            console.error("[continue]", e);
            const card = makeCard("alert-circle", "Error", "error-color");
            card.querySelector(".thought-body").textContent = String(e);
            addCard(pipelineRow, card);
        }
    } finally {
        abortController = null;
        setRunning(false);
        await loadSessions();
        loadSessionFiles(sessionId);
    }
}
const WORKFLOW_ORDER = [
    "prompt_refiner",
    "spec_agent",
    "planner_agent",
    "dependency_installer",
    "implementer_agent",
    "executor",
    "error_classifier",
    "reviewer_agent",
];

const WORKFLOW_LABELS = {
    prompt_refiner: "Refining prompt",
    spec_agent: "Extracting spec",
    planner_agent: "Building plan",
    dependency_installer: "Installing deps",
    implementer_agent: "Writing code",
    executor: "Running code",
    error_classifier: "Classifying error",
    reviewer_agent: "Reviewing output",
};

function workflowLabel(nodeName) {
    return WORKFLOW_LABELS[nodeName] || nodeName;
}

function workflowProgress(row) {
    const total = row.querySelectorAll(".pnode").length || 1;
    const done = row.querySelectorAll(".pnode.done").length;
    return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}

function ensureWorkflowHud(row) {
    let hud = row.querySelector(".workflow-hud");
    if (hud) return hud;

    hud = document.createElement("div");
    hud.className = "workflow-hud";
    hud.innerHTML = `
        <div class="workflow-hud-left">
            <span class="workflow-hud-label">Workflow</span>
            <span class="workflow-hud-state">Starting…</span>
        </div>
        <div class="workflow-hud-track">
            <div class="workflow-hud-fill"></div>
        </div>
    `;

    const body = row.querySelector(".ai-body");
    const bar = row.querySelector(".pipeline-bar");
    if (body && bar) body.insertBefore(hud, bar);
    return hud;
}

function updateWorkflowHud(row, state, label) {
    const hud = ensureWorkflowHud(row);
    const fill = hud.querySelector(".workflow-hud-fill");
    const text = hud.querySelector(".workflow-hud-state");
    const pct = workflowProgress(row);

    if (fill) fill.style.width = `${pct}%`;
    if (text) text.textContent = label || (
        state === "complete" ? "Completed" :
            state === "awaiting_user" ? "Needs guidance" :
                state === "error" ? "Stopped with issue" :
                    "Running"
    );

    hud.dataset.state = state || "running";
}

function settleWorkflow(row, state = "complete") {
    row.classList.add("workflow-settled");

    row.querySelectorAll(".pnode").forEach(n => {
        n.classList.remove("active");
        if (!n.classList.contains("error")) n.classList.add("done");
    });

    const bar = row.querySelector(".pipeline-bar");
    if (bar) {
        bar.classList.add("pipeline-compact");
    }

    updateWorkflowHud(row, state, state === "awaiting_user" ? "Needs guidance" : "Completed");
}

function syncSpecPanelFromData(data) {
    if (!data) return;

    const incomingSpec =
        data.spec ||
        data.project_spec ||
        data.specification ||
        null;

    const incomingPlan =
        data.plan ||
        data.build_plan ||
        null;

    if (incomingSpec) currentSpec = incomingSpec;
    if (incomingPlan) currentPlan = incomingPlan;

    if (currentSpec || currentPlan) {
        renderSpecPanel(currentSpec, currentPlan);
    }
}

// ── SSE Consumer ──────────────────────────────────────────
async function consumeSSE(res, pipelineRow) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        let boundary;
        while ((boundary = buf.indexOf("\n\n")) !== -1) {
            const line = buf.substring(0, boundary).trim();
            buf = buf.substring(boundary + 2);
            if (!line.startsWith("data: ")) continue;
            const jsonStr = line.slice(6).trim();
            if (!jsonStr) continue;
            let evt;
            try { evt = JSON.parse(jsonStr); } catch (e) {
                console.warn("[SSE parse]", e, jsonStr.slice(0, 80));
                continue;
            }
            try { handleEvent(evt, pipelineRow); }
            catch (ex) {
                console.error("[handleEvent]", ex);
                const card = makeCard("alert-circle", "Render Error", "error-color");
                card.querySelector(".thought-body").textContent = String(ex);
                addCard(pipelineRow, card);
            }
        }
    }
}

// ── Event Handler ─────────────────────────────────────────
function handleEvent(evt, pipelineRow) {
    const node = evt.node;
    const data = evt.data || {};
    syncSpecPanelFromData(data);

    if (node === "__error__") {
        const card = makeCard("alert-circle", "Error", "error-color");
        card.querySelector(".thought-body").textContent = data.error || "Unknown error";
        addCard(pipelineRow, card);
        return;
    }

    // ── Smart Mode Router (auto mode first message) ──────
    if (node === "smart_mode_router") {
        const decision = data.intent || data.mode || "plan";
        const labels = { chat: "Chat", plan: "Plan", fast: "Fast" };
        const icons = { chat: "message-circle", plan: "layout-list", fast: "zap" };
        const colors = { chat: "blue", plan: "primary", fast: "yellow" };

        const card = makeCard(icons[decision] || "sparkles", `Smart Router → ${labels[decision] || decision}`, "", false);
        const body = card.querySelector(".thought-body");
        body.innerHTML = `
            <div class="smart-route-decision">
                <div class="smart-route-badge ${decision}">
                    <i data-lucide="${icons[decision] || 'sparkles'}"></i>
                    <span>${labels[decision] || decision}</span>
                </div>
                <span class="smart-route-label">AutoDev intelligently chose <strong>${decision}</strong> mode for this message</span>
            </div>
        `;
        addCard(pipelineRow, card);

        // If it chose plan, show the pipeline bar; if chat, hide it
        if (decision === "chat") {
            const bar = pipelineRow.querySelector(".pipeline-bar");
            if (bar) bar.style.display = "none";
        }
        return;
    }

    // ── Conversation Router (follow-up messages) ─────────
    if (node === "conversation_router") {
        const intent = data.intent || "modify";
        const labels = { chat: "Chat", modify: "Modify", fix: "Fix", explain: "Explain", new_project: "New Project", execute: "Execute" };
        const icons = { chat: "message-circle", modify: "code-2", fix: "wrench", explain: "search", new_project: "layout-list", execute: "play" };

        const card = makeCard("git-branch", `Intent → ${labels[intent] || intent}`, "", false);
        card.querySelector(".thought-body").innerHTML = `
            <div class="smart-route-decision">
                <div class="smart-route-badge ${intent}">
                    <i data-lucide="${icons[intent] || 'git-branch'}"></i>
                    <span>${labels[intent] || intent}</span>
                </div>
                <span class="smart-route-label">Classified as <strong>${intent}</strong></span>
            </div>
        `;
        addCard(pipelineRow, card);

        if (intent === "chat" || intent === "explain") {
            const bar = pipelineRow.querySelector(".pipeline-bar");
            if (bar) bar.style.display = "none";
        }

        // ── NEW PROJECT: Reset frontend state so old project doesn't leak ──
        if (intent === "new_project") {
            currentFiles = {};
            currentSpec = null;
            currentPlan = null;
            currentSavedFiles = [];
            activeCodeFile = null;
            // Reset code panel
            codeContent.textContent = "";
            codeLang.textContent = "—";
            codeFileTabs.innerHTML = "";
            // Reset spec panel
            specPanel.innerHTML = `<div class="spec-empty">Extracting spec for new project…</div>`;
            // Reset execution output
            execOutput.innerHTML = `<span class="term-placeholder">New project pipeline running…</span>`;
            execOutput.className = "terminal";
            // Reset files panel
            filesList.innerHTML = "";
            filesPath.textContent = "";
            // Reset output console
            OutputConsole.close();
            OutputConsole.hideFab();
            OutputConsole.setTerminalOutput("");
            // Ensure pipeline bar is visible for the new project
            const bar = pipelineRow.querySelector(".pipeline-bar");
            if (bar) bar.style.display = "";
            // Mark prompt_refiner as next active node
            markNode(pipelineRow, "prompt_refiner", "active");
        }
        return;
    }

    // ── Chat Agent (casual conversation) ──────────────────
    if (node === "chat_agent") {
        const hist = data.history || [];
        const lastMsg = hist[hist.length - 1];
        const response = lastMsg ? lastMsg.content : "";

        if (response) {
            const card = makeCard("message-circle", "AutoDev", "", false);
            card.querySelector(".thought-body").textContent = response;
            card.querySelector(".thought-body").style.fontSize = "0.88rem";
            card.querySelector(".thought-body").style.lineHeight = "1.7";
            addCard(pipelineRow, card);
        }

        // Hide pipeline bar for chat responses
        const bar = pipelineRow.querySelector(".pipeline-bar");
        if (bar) bar.style.display = "none";
        return;
    }

    // ── Explain Agent ─────────────────────────────────────
    if (node === "explain_agent") {
        const hist = data.history || [];
        const lastMsg = hist[hist.length - 1];
        const response = lastMsg ? lastMsg.content : "";

        if (response) {
            const card = makeCard("book-open", "Code Explanation", "", false);
            card.querySelector(".thought-body").textContent = response;
            card.querySelector(".thought-body").style.fontSize = "0.85rem";
            card.querySelector(".thought-body").style.lineHeight = "1.7";
            addCard(pipelineRow, card);
        }
        return;
    }

    // Advance pipeline bar
    markNode(pipelineRow, node, "done");
    const next = NODE_NEXT[node];
    if (next) markNode(pipelineRow, next, "active");

    // ── Prompt Refiner ────────────────────────────────────
    if (node === "prompt_refiner" && data.refined_prompt) {
        const card = makeCard("wand-2", "Refined Prompt");
        card.querySelector(".thought-body").textContent = data.refined_prompt;
        card.classList.add("collapsed"); // start collapsed
        addCard(pipelineRow, card);
    }

    // ── Spec Agent ────────────────────────────────────────
    if (node === "spec_agent" && data.spec) {
        currentSpec = data.spec;
        const card = makeCard("file-search", "Project Spec");
        card.querySelector(".thought-body").innerHTML = buildSpecHtml(data.spec);
        addCard(pipelineRow, card);
        renderSpecPanel(data.spec, null);
    }

    // ── Planner Agent ───────────────────────────────────────
    if (node === "planner_agent" && data.plan) {
        const card = makeCard("layout-list", "Build Plan");
        card.querySelector(".thought-body").innerHTML = buildPlanHtml(data.plan);
        addCard(pipelineRow, card);
        renderSpecPanel(currentSpec, data.plan);
    }

    // ── Dependency Installer ──────────────────────────────
    if (node === "dependency_installer") {
        const success = data.dep_success;
        const log = data.dep_install_log || "";
        const pkgs = data.dependencies || [];

        const card = makeCard(
            success ? "check-circle-2" : "x-circle",
            `Dependencies — ${success ? "Installed" : "Failed"}`,
            success ? "" : "error-color"
        );
        card.querySelector(".thought-body").innerHTML = buildDepHtml(pkgs, success, log);
        addCard(pipelineRow, card);
    }

    // ── Implementer Agent ─────────────────────────────────
    if (node === "implementer_agent" && data.files) {
        currentFiles = data.files;
        const iters = (data.iterations || 0) + 1;
        const names = Object.keys(data.files);

        const card = makeCard("code-2", `Code — ${names.length} file(s) · Iteration ${iters}`, "code-style");
        card.querySelector(".thought-body").innerHTML = buildFilesPreviewHtml(data.files);
        addCard(pipelineRow, card);

        // Update code panel
        renderCodePanel(data.files);
    }

    // ── Executor ──────────────────────────────────────────
    if (node === "executor") {
        const report = data.exec_report || {};
        const output = data.exec_output || report.output || "(no output)";
        const success = data.exec_success || report.success || false;
        const errType = report.error_type || "none";

        const card = makeCard(
            success ? "terminal" : "alert-triangle",
            `Execution — ${success ? "Success" : errTypeLabel(errType)}`,
            "terminal-style"
        );
        card.querySelector(".thought-body").textContent = output;
        if (!success) card.querySelector(".thought-body").classList.add("txt-red");
        addCard(pipelineRow, card);

        // Update output panel
        setExecOutput(output, success);

        // Feed terminal output to the Output Console
        OutputConsole.setTerminalOutput(output);
        // Refresh output files (execution may have generated images/HTML/etc)
        OutputConsole.refresh(sessionId);
    }

    // ── Error Classifier ──────────────────────────────────
    if (node === "error_classifier" && data.error_classification) {
        const ec = data.error_classification;
        const card = makeCard("search", `Error Classification — ${ec.error_type}`, "error-color");
        card.querySelector(".thought-body").innerHTML = `
            <div><strong>Root Cause:</strong> ${esc(ec.root_cause)}</div>
            <div><strong>Affected Files:</strong> ${(ec.affected_files || []).join(", ") || "None"}</div>
            <div><strong>Strategy:</strong> ${esc(ec.suggested_strategy)}</div>
        `;
        addCard(pipelineRow, card);
    }

    /// ── Reviewer Agent ────────────────────────────────────
if (node === "reviewer_agent") {
    const verdict  = data.review_verdict || "RETRY";
    const scores   = data.quality_scores || {};
    const feedback = data.review_feedback || "";
    const status   = data.status || "running";
    const iters    = data.iterations || 0;
    const maxIt    = data.max_iterations || 6;

    const isPassed  = verdict === "PASS" || status === "success";
    const isWaiting = status === "awaiting_user";

    const label = isPassed
        ? "Passed"
        : isWaiting
        ? "Needs Guidance"
        : `Retrying (${iters}/${maxIt})`;

    const icon = isPassed
        ? "check-circle-2"
        : isWaiting
        ? "help-circle"
        : "refresh-cw";

    const card = makeCard(icon, `Review — ${label}`, "");
    const body = card.querySelector(".thought-body");
    body.innerHTML = buildReviewHtml(
        verdict,
        scores,
        feedback,
        data.retry_history || []
    );
    addCard(pipelineRow, card);

    //  SINGLE source of truth for workflow state
    if (isPassed || status === "failed") {
        settleWorkflow(pipelineRow, isPassed ? "complete" : "error");

        // optional visual fade (doesn't break state anymore)
        setTimeout(() => {
            const bar = pipelineRow.querySelector(".pipeline-bar");
            if (bar) {
                bar.style.transition = "all 0.5s ease";
                bar.style.opacity = "0";
                bar.style.transform = "translateY(-10px)";
                setTimeout(() => (bar.style.display = "none"), 500);
            }
        }, 1500);

    } else if (isWaiting) {
        updateWorkflowHud(
            pipelineRow,
            "awaiting_user",
            "Waiting for your guidance"
        );
    }


        // Show saved files
        if ((isPassed || status === "failed") && data.saved_files?.length) {
            currentSavedFiles = data.saved_files;
            const fcard = makeCard("folder-open", "Saved Files", "muted-color");
            fcard.querySelector(".thought-body").textContent = data.saved_files.join("\n");
            fcard.querySelector(".thought-body").style.fontFamily = "'JetBrains Mono', monospace";
            fcard.querySelector(".thought-body").style.fontSize = "0.73rem";
            addCard(pipelineRow, fcard);

            rerunBtn.style.display = "inline-flex";
            launchFileBtn.style.display = "inline-flex";
            launchBtn.style.display = "inline-flex";

            // Refresh output console with generated files
            OutputConsole.refresh(sessionId);

            // AUTO-OPEN only when the run produced real output artifacts.
            // Server/Streamlit projects often have terminal-only output, and opening
            // the floating console automatically there blocks the main workspace.
            if (status === "success") {
                setTimeout(async () => {
                    const snapshot = await OutputConsole.refresh(sessionId);
                    if ((snapshot.count || 0) > 0 && !OutputConsole.isOpen) {
                        OutputConsole.open();
                    }
                }, 800);
            }
        }

        // Show guidance banner if awaiting user
        if (isWaiting) {
            guidanceBanner.style.display = "block";
            guidanceBanner.scrollIntoView({ behavior: "smooth" });
        }
    }

    // ── Human Gate ────────────────────────────────────────
    if (node === "human_gate") {
        const card = makeCard("help-circle", `Human Gate — Provide Guidance`, "muted-color");
        card.querySelector(".thought-body").innerHTML = `
            <div style="white-space: pre-wrap;">${esc(data.human_gate_reason || "All automatic retries exhausted.")}</div>
        `;
        addCard(pipelineRow, card);

        guidanceBanner.style.display = "block";
        guidanceBanner.scrollIntoView({ behavior: "smooth" });
    }


    scrollChat();
}

// ── HTML Builders ─────────────────────────────────────────
function buildSpecHtml(spec) {
    const rows = [
        ["Type", `<span class="spec-type-badge">${esc(spec.output_type || "?")}</span>`],
        ["Entrypoint", `<span class="spec-tag">${esc(spec.entrypoint || "?")}</span>`],
        ["Files", (spec.expected_files || []).map(f => `<span class="spec-tag">${esc(f)}</span>`).join("")],
        ["Deps", (spec.dependencies || []).length
            ? spec.dependencies.map(d => `<span class="spec-tag">${esc(d)}</span>`).join("")
            : '<span style="color:var(--muted2)">none</span>'],
        ["Objective", esc(spec.objective || spec.problem_statement || "")],
    ];
    const grid = rows.map(([k, v]) =>
        `<div class="spec-key">${esc(k)}</div><div class="spec-val">${v}</div>`
    ).join("");
    return `<div class="spec-grid">${grid}</div>`;
}

function buildPlanHtml(plan) {
    const struct = plan.project_structure || {};
    const files = plan.file_order || Object.keys(struct);
    const pkgs = plan.packages || [];

    let html = '<div class="plan-files">';
    files.forEach(f => {
        html += `<div class="plan-file-row">
            <span class="plan-file-name">${esc(f)}</span>
            <span class="plan-file-desc">${esc(struct[f] || "")}</span>
        </div>`;
    });
    html += "</div>";

    if (pkgs.length) {
        html += '<div class="plan-pkg-list">' +
            pkgs.map(p => `<span class="plan-pkg">${esc(p)}</span>`).join("") +
            "</div>";
    }
    return html;
}

function buildDepHtml(pkgs, success, log) {
    if (!pkgs || pkgs.length === 0) {
        return `<div style="color:var(--muted);font-size:0.8rem;">No dependencies required.</div>`;
    }
    const rows = pkgs.map(p =>
        `<div class="dep-pkg-row">
            <i data-lucide="${success ? "check-circle" : "x-circle"}" class="dep-icon ${success ? "ok" : "fail"}"></i>
            <span>${esc(p)}</span>
        </div>`
    ).join("");

    const logHtml = log && !success
        ? `<div style="margin-top:0.5rem;color:var(--red);font-family:'JetBrains Mono',monospace;font-size:0.72rem;white-space:pre-wrap;">${esc(log.slice(0, 500))}</div>`
        : "";

    return rows + logHtml;
}

function buildFilesPreviewHtml(files) {
    let html = "";
    Object.entries(files).forEach(([name, code]) => {
        html += `<div class="code-file-header">${esc(name)}</div>`;
        // Show first 40 lines only in the card
        const lines = code.split("\n").slice(0, 40).join("\n");
        const truncated = code.split("\n").length > 40;
        html += esc(lines) + (truncated ? "\n… [truncated]" : "");
    });
    return html;
}

function buildReviewHtml(verdict, scores, feedback, retryHistory) {
    const isPassed = verdict === "PASS";
    let html = `<div class="verdict-badge ${isPassed ? "pass" : "retry"}">
        <i data-lucide="${isPassed ? "check-circle" : "refresh-cw"}"></i>
        ${isPassed ? "PASS" : "RETRY"}
    </div>`;

    // Score bars
    const dims = {
        spec_match: "Spec Match",
        file_completeness: "File Completeness",
        runtime_correctness: "Runtime",
        dependency_correctness: "Dependencies",
        output_quality: "Output Quality",
    };
    if (Object.keys(scores).length) {
        html += '<div class="score-grid">';
        Object.entries(dims).forEach(([key, label]) => {
            if (!(key in scores)) return;
            const v = scores[key];
            const cls = v >= 7 ? "high" : v >= 4 ? "mid" : "low";
            html += `<div class="score-row">
                <span class="score-label">${esc(label)}</span>
                <div class="score-bar-wrap"><div class="score-bar-fill ${cls}" style="width:${v * 10}%"></div></div>
                <span class="score-num ${cls}">${v}</span>
            </div>`;
        });
        html += "</div>";
    }

    if (feedback) {
        html += `<div style="margin-top:0.6rem;font-size:0.8rem;color:var(--muted)">${esc(feedback)}</div>`;
    }

    // Retry timeline
    if (retryHistory && retryHistory.length > 0) {
        html += '<div class="retry-timeline" style="margin-top:0.65rem">';
        retryHistory.slice(-4).forEach(r => {
            html += `<div class="retry-item">
                <span class="retry-num">#${r.attempt || "?"}</span>
                <span class="retry-strategy">${esc(r.strategy || r.fix_applied || "?")}</span>
                <span class="retry-diag">${esc((r.diagnosis || "").slice(0, 80))}</span>
            </div>`;
        });
        html += "</div>";
    }

    return html;
}

function errTypeLabel(t) {
    return {
        syntax: "Syntax Error",
        import: "Import Error",
        runtime: "Runtime Error",
        timeout: "Timeout",
        file_type_mismatch: "File Type Error",
        none: "Error",
    }[t] || "Error";
}

// ── Spec Panel ────────────────────────────────────────────
function renderSpecPanel(spec, plan) {
    if (!spec && !plan) {
        specPanel.innerHTML = `<div class="spec-empty">No spec yet.</div>`;
        return;
    }
    let html = "";

    if (spec) {
        html += `<div class="spec-section">
            <div class="spec-section-title">Problem</div>
            <div style="font-size:0.82rem;color:var(--text);line-height:1.6">${esc(spec.problem_statement || spec.objective || "")}</div>
        </div>`;

        html += `<div class="spec-section"><div class="spec-section-title">Spec</div>`;
        html += `<div class="spec-grid">
            <div class="spec-key">Type</div><div class="spec-val"><span class="spec-type-badge">${esc(spec.output_type || "?")}</span></div>
            <div class="spec-key">Entry</div><div class="spec-val"><span class="spec-tag">${esc(spec.entrypoint || "?")}</span></div>
            <div class="spec-key">Target</div><div class="spec-val">${esc(spec.execution_target || "terminal")}</div>
        </div></div>`;

        if ((spec.expected_files || []).length) {
            html += `<div class="spec-section">
                <div class="spec-section-title">Expected Files</div>
                <div>${spec.expected_files.map(f => `<span class="spec-tag">${esc(f)}</span>`).join(" ")}</div>
            </div>`;
        }
        if ((spec.dependencies || []).length) {
            html += `<div class="spec-section">
                <div class="spec-section-title">Dependencies</div>
                <div>${spec.dependencies.map(d => `<span class="spec-tag">${esc(d)}</span>`).join(" ")}</div>
            </div>`;
        }
        if ((spec.acceptance_criteria || []).length) {
            html += `<div class="spec-section">
                <div class="spec-section-title">Acceptance Criteria</div>
                ${spec.acceptance_criteria.map(c => `<div style="font-size:0.78rem;color:var(--muted);padding:1px 0">- ${esc(c)}</div>`).join("")}
            </div>`;
        }
    }

    if (plan) {
        const pkgs = plan.packages || [];
        if (pkgs.length) {
            html += `<div class="spec-section">
                <div class="spec-section-title">Packages</div>
                <div class="plan-pkg-list">${pkgs.map(p => `<span class="plan-pkg">${esc(p)}</span>`).join("")}</div>
            </div>`;
        }
        html += `<div class="spec-section">
            <div class="spec-section-title">Validation</div>
            <div style="font-size:0.78rem;color:var(--muted)">${esc(plan.validation_strategy || "")}</div>
        </div>`;
    }

    specPanel.innerHTML = html;
}

// ── Code Panel ────────────────────────────────────────────
function selectCodeFile(filename, options = {}) {
    const { switchToCode = false, persist = true } = options;
    if (!filename || !(filename in currentFiles)) return;

    activeCodeFile = filename;
    codeContent.textContent = currentFiles[filename];
    codeLang.textContent = filename.split(".").pop() || "text";

    if (codeFileTabs) {
        codeFileTabs.querySelectorAll(".code-file-tab").forEach(tab => {
            tab.classList.toggle("active", tab.dataset.filename === filename);
        });
    }

    updateFileListSelection(filename);
    if (persist) updateSessionUiState({ activeCodeFile: filename });
    if (switchToCode) switchTab("code");
}

function renderCodePanel(files, preferredFile = null) {
    if (!files || Object.keys(files).length === 0) {
        activeCodeFile = null;
        codeContent.textContent = "";
        codeLang.textContent = "—";
        codeFileTabs.innerHTML = "";
        updateFileListSelection("");
        return;
    }
    const names = Object.keys(files);
    codeFileTabs.innerHTML = "";

    names.forEach(name => {
        const tab = document.createElement("button");
        tab.className = "code-file-tab";
        tab.dataset.filename = name;
        tab.title = name;
        tab.textContent = name;
        tab.addEventListener("click", () => selectCodeFile(name));
        codeFileTabs.appendChild(tab);
    });

    const storedFile = preferredFile || getSessionUiState().activeCodeFile || activeCodeFile;
    const nextFile = storedFile && files[storedFile] != null ? storedFile : names[0];
    selectCodeFile(nextFile, { persist: false });
}

// ── Files Panel ───────────────────────────────────────────
async function loadSessionFiles(id) {
    try {
        const res = await apiFetch(`/session_files/${id}`);
        const data = await res.json();
        renderFilesList(data.files, data.dir);
    } catch (e) { /* silent */ }
}

function renderFilesList(files, dir) {
    filesPath.textContent = dir || "";
    filesList.innerHTML = "";
    if (!files || files.length === 0) {
        filesList.innerHTML = `<div style="padding:1rem;color:var(--muted2);font-size:0.8rem;">No files yet.</div>`;
        return;
    }
    files.forEach(f => {
        const item = document.createElement("div");
        item.className = "file-item";
        item.dataset.filename = f.name;
        const extIcon = f.name.endsWith(".html") ? "globe"
            : f.name.endsWith(".py") ? "file-code"
                : f.name.endsWith(".css") ? "palette"
                    : f.name.endsWith(".js") ? "braces"
                        : f.name.endsWith(".sh") ? "terminal"
                            : "file";
        item.innerHTML = `<i data-lucide="${extIcon}"></i><span>${esc(f.name)}</span>`;
        item.addEventListener("click", () => {
            if (!(f.name in currentFiles)) currentFiles[f.name] = f.content;
            selectCodeFile(f.name, { switchToCode: true });
        });
        filesList.appendChild(item);
        try { lucide.createIcons(); } catch (e) { }
    });
    updateFileListSelection(activeCodeFile || "");
}

// ── Actions ───────────────────────────────────────────────
async function refinePrompt() {
    const task = taskInput.value.trim();
    if (!task) return;
    if (!ensureRuntimeConfigured()) return;
    refineBtn.disabled = true;
    appendRuntimeLog("refine_prompt", `Requested prompt refinement with provider=${modelSelect.value}`);
    try {
        const res = await apiFetch("/refine_prompt", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ task, model: modelSelect.value }),
        });
        const data = await res.json();
        taskInput.value = data.refined || task;
        autoResize(taskInput);
    } catch (e) { console.warn("[refine]", e); }
    finally { refineBtn.disabled = false; }
}

async function rerunFile() {
    rerunBtn.disabled = true;
    setExecOutput("Re-running…", null);
    try {
        const res = await apiFetch("/rerun_file", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
        });
        const data = await res.json();
        setExecOutput(data.output || "(no output)", data.success);
        // Feed to output console
        OutputConsole.setTerminalOutput(data.output || "(no output)");
        OutputConsole.refresh(sessionId);
    } catch (e) {
        setExecOutput(String(e), false);
    } finally { rerunBtn.disabled = false; }
}

async function launchFile(fp) {
    if (!fp) { launchSession(); return; }
    try {
        await apiFetch("/run_file", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filepath: fp }),
        });
    } catch (e) { console.warn("[launch]", e); }
}

async function launchSession() {
    try {
        await apiFetch("/launch_session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
        });
    } catch (e) { console.warn("[launch_session]", e); }
}

async function wipeRag() {
    if (!confirm("Wipe all AI memory? This clears all session RAG data.")) return;
    try {
        await apiFetch("/wipe_rag", { method: "POST" });
        wipeRagBtn.style.color = "var(--green)";
        setTimeout(() => wipeRagBtn.style.color = "", 1500);
    } catch (e) { console.warn("[wipe_rag]", e); }
}

function copyCode() {
    const text = codeContent.textContent;
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
        copyCodeBtn.innerHTML = `<i data-lucide="check"></i>`;
        try { lucide.createIcons(); } catch (e) { }
        setTimeout(() => {
            copyCodeBtn.innerHTML = `<i data-lucide="copy"></i>`;
            try { lucide.createIcons(); } catch (e) { }
        }, 1200);
    });
}

function startNewProject() {
    const preserveSnapshot = hasProjectSnapshot();
    sessionId = newSessionId();
    clearChat(false);
    guidanceBanner.style.display = "none";
    launchBtn.style.display = "none";
    rerunBtn.style.display = "none";
    launchFileBtn.style.display = "none";
    taskInput.value = "";
    autoResize(taskInput);
    guidanceInput.value = "";
    OutputConsole.close();
    OutputConsole.hideFab();
    updateSessionUiState({ activePanelTab: "output", activeCodeFile: "" });
    document.querySelectorAll(".session-item").forEach(el => el.classList.remove("active"));

    if (!preserveSnapshot) {
        currentFiles = {};
        currentSpec = null;
        currentPlan = null;
        currentSavedFiles = [];
        activeCodeFile = null;
        rightPanel.style.display = "none";
        setExecOutput("");
        codeContent.textContent = "";
        codeLang.textContent = "—";
        codeFileTabs.innerHTML = "";
        filesList.innerHTML = "";
        filesPath.textContent = "";
        specPanel.innerHTML = `<div class="spec-empty">No spec yet. Run a task to see the project specification.</div>`;
    }

    taskInput.focus();
}

function setRunning(running) {
    isRunning = running;
    runBtn.disabled = running;
    taskInput.disabled = running;
    continueBtn.disabled = running;
    if (running) {
        runBtn.style.display = "none";
        stopBtn.style.display = "inline-flex";
    } else {
        runBtn.style.display = "inline-flex";
        stopBtn.style.display = "none";
    }
}

// ── Utilities ─────────────────────────────────────────────
function esc(s) {
    if (s == null) return "";
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// ── Boot ──────────────────────────────────────────────────
init();

// =====================================================
// Dynamic Output Console
// =====================================================

const OutputConsole = (() => {
    // State
    let outputFiles = [];
    let activeFile = null;
    let isOpen = false;
    let isFullscreen = false;
    let terminalText = "";
    let lastSessionId = null;
    let pollTimer = null;
    let activeView = "empty";

    // DOM refs (lazy-init)
    const el = {};
    function initRefs() {
        el.fab = document.getElementById("outputFab");
        el.badge = document.getElementById("fabBadge");
        el.overlay = document.getElementById("outputOverlay");
        el.console = document.getElementById("outputConsole");
        el.title = document.getElementById("consoleTitle");
        el.consoleBadge = document.getElementById("consoleBadge");
        el.navList = document.getElementById("consoleNavList");
        el.preview = document.getElementById("consolePreview");
        el.empty = document.getElementById("consoleEmpty");
        el.footerInfo = document.getElementById("consoleFooterInfo");
        el.imageViewer = document.getElementById("consoleImageViewer");
        el.image = document.getElementById("consoleImage");
        el.htmlViewer = document.getElementById("consoleHtmlViewer");
        el.iframe = document.getElementById("consoleIframe");
        el.videoViewer = document.getElementById("consoleVideoViewer");
        el.video = document.getElementById("consoleVideo");
        el.audioViewer = document.getElementById("consoleAudioViewer");
        el.audio = document.getElementById("consoleAudio");
        el.textViewer = document.getElementById("consoleTextViewer");
        el.textEl = document.getElementById("consoleText");
        el.terminalViewer = document.getElementById("consoleTerminalViewer");
        el.terminalOutput = document.getElementById("consoleTerminalOutput");
        el.pdfViewer = document.getElementById("consolePdfViewer");
        el.pdf = document.getElementById("consolePdf");
        el.galleryViewer = document.getElementById("consoleGalleryViewer");
        el.galleryGrid = document.getElementById("galleryGrid");
        el.terminalBtn = document.getElementById("consoleTerminalBtn");
    }

    function bindEvents() {
        el.fab.addEventListener("click", toggle);
        document.getElementById("consoleCloseBtn").addEventListener("click", close);
        document.getElementById("consoleFullscreenBtn").addEventListener("click", toggleFullscreen);
        document.getElementById("consoleRefreshBtn").addEventListener("click", () => refresh(sessionId));
        document.getElementById("consoleExternalBtn").addEventListener("click", openExternal);
        document.getElementById("consoleLaunchBtn").addEventListener("click", () => launchSession());
        el.terminalBtn.addEventListener("click", showTerminal);
        el.image.addEventListener("click", toggleZoom);

        el.overlay.addEventListener("click", (e) => {
            if (e.target === el.overlay) close();
        });

        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape" && isOpen) close();
        });
    }

    // ── Public API ───────────────────────────────────────

    function initConsole() {
        initRefs();
        bindEvents();
    }

    function toggle() {
        if (isOpen) close();
        else open();
    }

    function open() {
        isOpen = true;
        el.overlay.style.display = "flex";
        refresh(sessionId);
        startPolling();
    }

    function close() {
        isOpen = false;
        el.overlay.style.display = "none";
        el.console.classList.remove("fullscreen");
        isFullscreen = false;
        stopPolling();
        el.image.classList.remove("zoomed");
    }

    function toggleFullscreen() {
        isFullscreen = !isFullscreen;
        el.console.classList.toggle("fullscreen", isFullscreen);
        const icon = document.querySelector("#consoleFullscreenBtn i");
        if (icon) {
            icon.setAttribute("data-lucide", isFullscreen ? "minimize-2" : "maximize-2");
            try { lucide.createIcons(); } catch (e) {}
        }
    }

    function toggleZoom() {
        el.image.classList.toggle("zoomed");
    }

    function showTerminal() {
        activeView = "terminal";
        activeFile = null;
        hideAllViewers();
        el.navList.querySelectorAll(".console-nav-item").forEach(i => i.classList.remove("active"));
        el.terminalBtn.classList.add("active");
        el.terminalViewer.style.display = "flex";
        el.terminalOutput.textContent = terminalText || "(no terminal output yet)";
        el.footerInfo.textContent = "Terminal output";
    }

    // ── Fetch & Render ──────────────────────────────────

    async function refresh(sid) {
        if (!sid) return { count: 0, activePath: "" };
        lastSessionId = sid;

        try {
            const res = await apiFetch(`/output_files/${sid}`);
            const data = await res.json();
            outputFiles = data.files || [];
        } catch (e) {
            console.warn("[OutputConsole] fetch error:", e);
            outputFiles = [];
        }

        updateFab();
        if (isOpen) renderNav();
        return {
            count: outputFiles.length,
            activePath: activeFile ? activeFile.path : "",
        };
    }

    function updateFab() {
        const count = outputFiles.length;
        el.fab.style.display = "flex";

        if (count > 0) {
            el.badge.textContent = count;
            el.badge.dataset.count = count;
            el.fab.classList.add("has-output");
        } else {
            el.badge.textContent = "0";
            el.badge.dataset.count = "0";
            el.fab.classList.remove("has-output");
        }
    }

    function showFab() {
        el.fab.style.display = "flex";
    }

    function hideFab() {
        el.fab.style.display = "none";
    }

    function renderNav() {
        const previousFilePath = activeFile ? activeFile.path : "";
        const previousView = activeView;
        el.navList.innerHTML = "";
        el.consoleBadge.textContent = `${outputFiles.length} file${outputFiles.length !== 1 ? "s" : ""}`;
        el.terminalBtn.classList.remove("active");

        if (outputFiles.length === 0) {
            hideAllViewers();
            if (terminalText) {
                showTerminal();
            } else {
                activeView = "empty";
                el.empty.style.display = "flex";
                el.footerInfo.textContent = "No output files";
            }
            return;
        }

        el.empty.style.display = "none";

        outputFiles.forEach((file, idx) => {
            const item = document.createElement("button");
            item.className = "console-nav-item";
            const iconName = getFileIcon(file.category);
            item.innerHTML = `
                <i data-lucide="${iconName}"></i>
                <span class="console-nav-item-name">${esc(file.name)}</span>
                <span class="console-nav-item-cat ${file.category}">${file.category}</span>
            `;
            item.addEventListener("click", () => selectFile(file, idx));
            el.navList.appendChild(item);
        });

        try { lucide.createIcons(); } catch (e) {}

        const images = outputFiles.filter(f => f.category === "image");
        const selectedFile = previousFilePath
            ? outputFiles.find(file => file.path === previousFilePath)
            : null;

        if (previousView === "terminal" && terminalText) {
            showTerminal();
        } else if (selectedFile) {
            selectFile(selectedFile, outputFiles.indexOf(selectedFile));
        } else if (previousView === "gallery" && images.length > 1) {
            showGallery(images);
        } else if (images.length > 1) {
            showGallery(images);
        } else if (outputFiles.length > 0) {
            selectFile(outputFiles[0], 0);
        }
    }

    function selectFile(file, idx) {
        activeView = "file";
        activeFile = file;
        el.navList.querySelectorAll(".console-nav-item").forEach((item, i) => {
            item.classList.toggle("active", i === idx);
        });
        el.terminalBtn.classList.remove("active");
        hideAllViewers();
        renderFile(file);
    }

    function renderFile(file) {
        const url = apiUrl(file.url) + "?t=" + Date.now();

        switch (file.category) {
            case "image":
                el.imageViewer.style.display = "flex";
                el.image.src = url;
                el.image.alt = file.name;
                el.image.classList.remove("zoomed");
                el.footerInfo.textContent = `${file.name} \u00b7 ${formatSize(file.size)}`;
                break;
            case "html":
                el.htmlViewer.style.display = "flex";
                el.iframe.src = url;
                el.footerInfo.textContent = `${file.name} \u00b7 Interactive HTML`;
                break;
            case "video":
                el.videoViewer.style.display = "flex";
                el.video.src = url;
                el.video.load();
                el.footerInfo.textContent = `${file.name} \u00b7 ${formatSize(file.size)}`;
                break;
            case "audio":
                el.audioViewer.style.display = "flex";
                el.audio.src = url;
                el.audio.load();
                el.footerInfo.textContent = `${file.name} \u00b7 Audio`;
                break;
            case "pdf":
                el.pdfViewer.style.display = "flex";
                el.pdf.src = url;
                el.footerInfo.textContent = `${file.name} \u00b7 PDF Document`;
                break;
            default:
                el.textViewer.style.display = "flex";
                el.footerInfo.textContent = `${file.name} \u00b7 ${formatSize(file.size)}`;
                fetchTextContent(url);
                break;
        }
    }

    async function fetchTextContent(url) {
        try {
            const res = await apiFetch(url);
            const text = await res.text();
            el.textEl.textContent = text.slice(0, 50000);
        } catch (e) {
            el.textEl.textContent = `Error loading file: ${e}`;
        }
    }

    function showGallery(images) {
        activeView = "gallery";
        activeFile = null;
        hideAllViewers();
        el.galleryViewer.style.display = "flex";
        el.galleryGrid.innerHTML = "";

        images.forEach((file) => {
            const item = document.createElement("div");
            item.className = "gallery-item";
            item.innerHTML = `
                <img src="${apiUrl(file.url)}?t=${Date.now()}" alt="${esc(file.name)}" loading="lazy" />
                <div class="gallery-item-label">${esc(file.name)}</div>
            `;
            item.addEventListener("click", () => {
                const navIdx = outputFiles.indexOf(file);
                selectFile(file, navIdx);
            });
            el.galleryGrid.appendChild(item);
        });

        el.footerInfo.textContent = `${images.length} images \u00b7 Gallery View`;
        el.navList.querySelectorAll(".console-nav-item").forEach(i => i.classList.remove("active"));
    }

    function hideAllViewers() {
        el.empty.style.display = "none";
        el.imageViewer.style.display = "none";
        el.htmlViewer.style.display = "none";
        el.videoViewer.style.display = "none";
        el.audioViewer.style.display = "none";
        el.textViewer.style.display = "none";
        el.terminalViewer.style.display = "none";
        el.pdfViewer.style.display = "none";
        el.galleryViewer.style.display = "none";
        el.image.classList.remove("zoomed");
    }

    function openExternal() {
        if (activeFile) {
            window.open(apiUrl(activeFile.url), "_blank");
        } else {
            launchSession();
        }
    }

    function setTerminalOutput(text) {
        terminalText = text || "";
        if (isOpen && el.terminalViewer.style.display !== "none") {
            el.terminalOutput.textContent = terminalText || "(no terminal output yet)";
        }
    }

    function startPolling() {
        stopPolling();
        pollTimer = setInterval(() => {
            if (isOpen && lastSessionId) {
                refresh(lastSessionId);
            }
        }, 3000);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function getFileIcon(category) {
        const icons = {
            image: "image",
            html: "globe",
            video: "film",
            audio: "music",
            pdf: "file-text",
            text: "file-text",
        };
        return icons[category] || "file";
    }

    function formatSize(bytes) {
        if (bytes < 1024) return bytes + " B";
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
        return (bytes / 1048576).toFixed(1) + " MB";
    }

    return {
        init: initConsole,
        open,
        close,
        toggle,
        refresh,
        showFab,
        hideFab,
        setTerminalOutput,
        updateFab,
        get isOpen() { return isOpen; },
    };
})();

// Initialize the output console after DOM is ready
OutputConsole.init();

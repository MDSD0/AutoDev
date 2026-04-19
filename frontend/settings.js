const settingsStore = window.AutoDevSettings;
let settingsState = settingsStore.loadSettings();
let bootstrapState = null;
const API_BASE_STORAGE_KEY = "autodev-api-base-v1";
const USER_SCOPE_STORAGE_KEY = "autodev-user-scope-v1";
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765";

function getOrCreateUserScope() {
    try {
        const existing = localStorage.getItem(USER_SCOPE_STORAGE_KEY);
        if (existing) return existing;
        const created = "u_" + Math.random().toString(36).slice(2, 14);
        localStorage.setItem(USER_SCOPE_STORAGE_KEY, created);
        return created;
    } catch (e) {
        console.warn("[settings-user-scope]", e);
        return "u_browser";
    }
}

function normalizeApiBase(value) {
    return (value || "").trim().replace(/\/+$/, "");
}

function defaultApiBase() {
    // Desktop mode: always use the local backend
    if (window.electronAPI && window.electronAPI.isDesktop) {
        return "http://127.0.0.1:8000";
    }
    return "";
}

function getApiBase() {
    try {
        return normalizeApiBase(localStorage.getItem(API_BASE_STORAGE_KEY) || "") || defaultApiBase();
    } catch (e) {
        console.warn("[settings-api-base]", e);
        return defaultApiBase();
    }
}

function apiUrl(url) {
    if (!url) return getApiBase() || "";
    if (/^https?:\/\//i.test(url)) return url;
    const base = getApiBase();
    return base ? `${base}${url}` : url;
}

function apiFetch(url, options = {}) {
    const headers = settingsStore.buildRuntimeHeaders(options.headers || {}, settingsStore.loadSettings());
    headers["X-Autodev-User"] = getOrCreateUserScope();
    return fetch(apiUrl(url), {
        ...options,
        headers,
    });
}

const settingsRefs = {
    themeToggle: document.getElementById("themeToggle"),
    themeToggleIcon: document.getElementById("themeToggleIcon"),
    refreshSettingsBtn: document.getElementById("refreshSettingsBtn"),
    restoreSettingsBtn: document.getElementById("restoreSettingsBtn"),
    defaultProviderSelect: document.getElementById("defaultProviderSelect"),
    workflowDepthSelect: document.getElementById("workflowDepthSelect"),
    defaultRetriesInput: document.getElementById("defaultRetriesInput"),
    defaultRetriesLabel: document.getElementById("defaultRetriesLabel"),
    defaultRefineToggle: document.getElementById("defaultRefineToggle"),
    expertModeToggle: document.getElementById("expertModeToggle"),
    skipSpecToggle: document.getElementById("skipSpecToggle"),
    skipReviewToggle: document.getElementById("skipReviewToggle"),
    expertFlags: document.getElementById("expertFlags"),
    settingsSummary: document.getElementById("settingsSummary"),
    providerGrid: document.getElementById("providerGrid"),
    roleAssignments: document.getElementById("roleAssignments"),
    secretsGrid: document.getElementById("secretsGrid"),
    endpointForm: document.getElementById("endpointForm"),
    endpointName: document.getElementById("endpointName"),
    endpointUrl: document.getElementById("endpointUrl"),
    endpointModelId: document.getElementById("endpointModelId"),
    endpointAuthHeader: document.getElementById("endpointAuthHeader"),
    endpointApiKey: document.getElementById("endpointApiKey"),
    endpointType: document.getElementById("endpointType"),
    endpointStreaming: document.getElementById("endpointStreaming"),
    endpointToolUse: document.getElementById("endpointToolUse"),
    endpointJsonMode: document.getElementById("endpointJsonMode"),
    testEndpointBtn: document.getElementById("testEndpointBtn"),
    endpointList: document.getElementById("endpointList"),
    ollamaModelInput: document.getElementById("ollamaModelInput"),
    ollamaBaseUrlInput: document.getElementById("ollamaBaseUrlInput"),
    listOllamaBtn: document.getElementById("listOllamaBtn"),
    pullOllamaBtn: document.getElementById("pullOllamaBtn"),
    useOllamaBtn: document.getElementById("useOllamaBtn"),
    ollamaStatus: document.getElementById("ollamaStatus"),
    ollamaList: document.getElementById("ollamaList"),
    ggufModelPathInput: document.getElementById("ggufModelPathInput"),
    hfReferenceInput: document.getElementById("hfReferenceInput"),
    hfDownloadPatternInput: document.getElementById("hfDownloadPatternInput"),
    hfLocalDirInput: document.getElementById("hfLocalDirInput"),
    downloadHfModelBtn: document.getElementById("downloadHfModelBtn"),
    llamaCppCommandInput: document.getElementById("llamaCppCommandInput"),
    llamaCppPortInput: document.getElementById("llamaCppPortInput"),
    launchLlamaCppBtn: document.getElementById("launchLlamaCppBtn"),
    stopLlamaCppBtn: document.getElementById("stopLlamaCppBtn"),
    llamaCppStatus: document.getElementById("llamaCppStatus"),
    localEndpointUrlInput: document.getElementById("localEndpointUrlInput"),
    localEndpointModelInput: document.getElementById("localEndpointModelInput"),
    localEndpointApiKeyInput: document.getElementById("localEndpointApiKeyInput"),
    localEndpointAuthHeaderInput: document.getElementById("localEndpointAuthHeaderInput"),
    testLocalEndpointBtn: document.getElementById("testLocalEndpointBtn"),
    useLocalEndpointBtn: document.getElementById("useLocalEndpointBtn"),
    localEndpointStatus: document.getElementById("localEndpointStatus"),
    clearLogsBtn: document.getElementById("clearLogsBtn"),
    logList: document.getElementById("logList"),
};

const SECRET_FIELDS = [
    {
        id: "geminiApiKey",
        label: "Gemini API key",
        provider: "gemini",
        description: "Saved to local runtime settings and used by the backend runner for Gemini calls.",
        placeholder: "AIza...",
        type: "password",
    },
    {
        id: "groqApiKey",
        label: "Groq API key",
        provider: "groq",
        description: "Saved to local runtime settings and used by the backend runner for Groq calls.",
        placeholder: "gsk_...",
        type: "password",
    },
    {
        id: "groqApiKey2",
        label: "Groq fallback API key",
        provider: "groq_2",
        description: "Optional second Groq key for fallback routing and alternate roles.",
        placeholder: "gsk_...",
        type: "password",
    },
    {
        id: "huggingFaceApiKey",
        label: "Hugging Face token",
        provider: "custom",
        description: "Used by the backend for Hugging Face downloads or protected compatible endpoints.",
        placeholder: "hf_...",
        type: "password",
    },
    {
        id: "ollamaBaseUrl",
        label: "Ollama / bridge URL",
        provider: "ollama",
        description: "Local Ollama bridge URL used by project-wide local model routing.",
        placeholder: "http://localhost:11434",
        type: "url",
    },
];

async function initSettingsPage() {
    applyTheme(getPreferredTheme());
    bindStaticEvents();
    await refreshRuntimeSettings(false);
    await refreshBootstrap(false);
    settingsStore.appendLog({
        type: "settings_opened",
        message: "Opened settings page",
    });
    renderAll();
}

function bindStaticEvents() {
    settingsRefs.themeToggle?.addEventListener("click", toggleTheme);
    settingsRefs.refreshSettingsBtn?.addEventListener("click", () => refreshBootstrap(true));
    settingsRefs.restoreSettingsBtn?.addEventListener("click", restoreDefaults);
    settingsRefs.defaultProviderSelect?.addEventListener("change", handleWorkflowDefaultsChange);
    settingsRefs.workflowDepthSelect?.addEventListener("change", handleWorkflowDefaultsChange);
    settingsRefs.defaultRetriesInput?.addEventListener("input", handleWorkflowDefaultsChange);
    settingsRefs.defaultRefineToggle?.addEventListener("change", handleWorkflowDefaultsChange);
    settingsRefs.expertModeToggle?.addEventListener("change", handleWorkflowDefaultsChange);
    settingsRefs.skipSpecToggle?.addEventListener("change", handleWorkflowDefaultsChange);
    settingsRefs.skipReviewToggle?.addEventListener("change", handleWorkflowDefaultsChange);
    settingsRefs.endpointForm?.addEventListener("submit", handleEndpointSubmit);
    settingsRefs.testEndpointBtn?.addEventListener("click", testDraftEndpoint);
    settingsRefs.providerGrid?.addEventListener("click", handleProviderAction);
    settingsRefs.providerGrid?.addEventListener("change", handleProviderChange);
    settingsRefs.roleAssignments?.addEventListener("change", handleRoleAssignmentChange);
    settingsRefs.secretsGrid?.addEventListener("click", handleSecretAction);
    settingsRefs.secretsGrid?.addEventListener("change", handleSecretChange);
    settingsRefs.endpointList?.addEventListener("click", handleEndpointListAction);
    settingsRefs.ollamaList?.addEventListener("click", handleOllamaListAction);
    settingsRefs.listOllamaBtn?.addEventListener("click", listOllamaModels);
    settingsRefs.pullOllamaBtn?.addEventListener("click", pullOllamaModel);
    settingsRefs.useOllamaBtn?.addEventListener("click", useOllamaModelForProject);
    settingsRefs.downloadHfModelBtn?.addEventListener("click", downloadHuggingFaceModel);
    settingsRefs.launchLlamaCppBtn?.addEventListener("click", launchLlamaCppRunner);
    settingsRefs.stopLlamaCppBtn?.addEventListener("click", stopLlamaCppRunner);
    settingsRefs.testLocalEndpointBtn?.addEventListener("click", testSavedLocalEndpoint);
    settingsRefs.useLocalEndpointBtn?.addEventListener("click", useLocalEndpointForProject);
    settingsRefs.clearLogsBtn?.addEventListener("click", () => {
        settingsStore.clearLogs();
        renderLogs();
    });
}

async function refreshRuntimeSettings(logAction = true) {
    try {
        const res = await apiFetch("/settings/runtime");
        const data = await res.json();
        settingsState = settingsStore.mergeServerSettings(data, settingsStore.loadSettings());
        if (logAction) {
            settingsStore.appendLog({
                type: "runtime_settings",
                message: "Loaded backend defaults without overwriting browser-only secrets",
            });
        }
    } catch (e) {
        console.warn("[runtime-settings]", e);
        settingsState = settingsStore.loadSettings();
        if (logAction) {
            settingsStore.appendLog({
                type: "runtime_settings_error",
                message: `Failed to load runtime settings: ${e}`,
            });
        }
    }
}

async function persistRuntimeSettings(logMessage = "") {
    settingsState = settingsStore.loadSettings();
    try {
        const res = await apiFetch("/settings/runtime", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ settings: settingsStore.stripSecretsFromRuntime(settingsState) }),
        });
        const data = await res.json();
        if (data?.settings) {
            settingsState = settingsStore.mergeServerSettings(data.settings, settingsStore.loadSettings());
        }
        if (logMessage) {
            settingsStore.appendLog({
                type: "runtime_settings_saved",
                message: logMessage || "Saved runtime defaults to the backend without persisting secrets.",
            });
        }
    } catch (e) {
        settingsStore.appendLog({
            type: "runtime_settings_error",
            message: `Failed to save runtime settings: ${e}`,
        });
    }
}

function getPreferredTheme() {
    try {
        const savedTheme = localStorage.getItem("autodev-theme");
        if (savedTheme === "light" || savedTheme === "dark") return savedTheme;
    } catch (e) {
        console.warn("[settings-theme]", e);
    }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    if (settingsRefs.themeToggle) {
        const nextTheme = theme === "light" ? "dark" : "light";
        settingsRefs.themeToggle.title = `Switch to ${nextTheme} theme`;
        settingsRefs.themeToggle.setAttribute("aria-label", `Switch to ${nextTheme} theme`);
    }
    if (settingsRefs.themeToggleIcon) {
        settingsRefs.themeToggleIcon.setAttribute("data-lucide", theme === "light" ? "moon-star" : "sun-medium");
    }
    lucide.createIcons();
}

function toggleTheme() {
    const nextTheme = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(nextTheme);
    try {
        localStorage.setItem("autodev-theme", nextTheme);
    } catch (e) {
        console.warn("[settings-theme]", e);
    }
}

async function refreshBootstrap(logAction = true) {
    try {
        const res = await apiFetch("/settings/bootstrap");
        bootstrapState = await res.json();
        if (logAction) {
            settingsStore.appendLog({
                type: "provider_bootstrap",
                message: "Refreshed provider bootstrap data from backend",
            });
        }
    } catch (e) {
        console.warn("[settings-bootstrap]", e);
        bootstrapState = null;
        if (logAction) {
            settingsStore.appendLog({
                type: "provider_bootstrap_error",
                message: `Failed to refresh bootstrap data: ${e}`,
            });
        }
    }
    renderAll();
}

function renderAll() {
    settingsState = settingsStore.loadSettings();
    renderWorkflowDefaults();
    renderSummary();
    renderProviderRegistry();
    renderLocalModelLab();
    renderRoleAssignments();
    renderSecrets();
    renderEndpoints();
    renderLogs();
    lucide.createIcons();
}

function handleWorkflowDefaultsChange() {
    const depth = settingsRefs.workflowDepthSelect.value;
    const preset = settingsStore.getPreset(depth);
    settingsStore.updateSettings((draft) => {
        draft.providerRegistry.defaultProvider = settingsRefs.defaultProviderSelect.value;
        draft.workflowDefaults.defaultProvider = settingsRefs.defaultProviderSelect.value;
        draft.workflowDefaults.workflowDepth = depth;
        draft.workflowDefaults.mode = preset.mode;
        draft.workflowDefaults.maxRetries = Number(settingsRefs.defaultRetriesInput.value);
        draft.workflowDefaults.refinePrompt = settingsRefs.defaultRefineToggle.checked;
        draft.workflowDefaults.expertMode = settingsRefs.expertModeToggle.checked;
        draft.workflowDefaults.skipSpec = settingsRefs.skipSpecToggle.checked;
        draft.workflowDefaults.skipReview = settingsRefs.skipReviewToggle.checked;
    });
    settingsStore.appendLog({
        type: "workflow_defaults",
        message: `Saved workflow defaults (${depth}, ${settingsRefs.defaultProviderSelect.value}, ${settingsRefs.defaultRetriesInput.value} retries)`,
    });
    persistRuntimeSettings("Saved workflow defaults to backend runtime settings");
    renderAll();
}

function renderWorkflowDefaults() {
    const providerOptions = getSelectableProviders(false);
    const currentDefaults = settingsStore.getRunnerDefaults(settingsState);

    settingsRefs.defaultProviderSelect.innerHTML = providerOptions
        .map((option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}</option>`)
        .join("");
    settingsRefs.defaultProviderSelect.value =
        settingsState.providerRegistry.defaultProvider || currentDefaults.provider;

    settingsRefs.workflowDepthSelect.innerHTML = settingsStore.WORKFLOW_PRESETS
        .map((preset) => `<option value="${preset.id}">${preset.label} — ${preset.description}</option>`)
        .join("");
    settingsRefs.workflowDepthSelect.value = settingsState.workflowDefaults.workflowDepth;

    settingsRefs.defaultRetriesInput.value = String(settingsState.workflowDefaults.maxRetries);
    settingsRefs.defaultRetriesLabel.textContent = `${settingsState.workflowDefaults.maxRetries} retries`;
    settingsRefs.defaultRefineToggle.checked = settingsState.workflowDefaults.refinePrompt !== false;
    settingsRefs.expertModeToggle.checked = !!settingsState.workflowDefaults.expertMode;
    settingsRefs.skipSpecToggle.checked = !!settingsState.workflowDefaults.skipSpec;
    settingsRefs.skipReviewToggle.checked = !!settingsState.workflowDefaults.skipReview;
    settingsRefs.expertFlags.style.display = settingsState.workflowDefaults.expertMode ? "grid" : "none";
}

function renderSummary() {
    const defaults = settingsStore.getRunnerDefaults(settingsState);
    const providerCount = bootstrapState?.available_providers?.length || 0;
    const summaryItems = [
        {
            label: "Backend version",
            value: bootstrapState?.version || "Unavailable",
        },
        {
            label: "Available providers",
            value: providerCount ? `${providerCount} detected` : "No live bootstrap",
        },
        {
            label: "Main-page defaults",
            value: `${defaults.provider} · ${defaults.mode} · ${defaults.maxRetries} retries`,
        },
        {
            label: "Pipeline nodes",
            value: bootstrapState?.pipeline_nodes?.join(" → ") || "Unavailable",
        },
    ];

    settingsRefs.settingsSummary.innerHTML = summaryItems
        .map((item) => `
            <div class="summary-item">
                <div class="summary-item-label">${escapeHtml(item.label)}</div>
                <div class="summary-item-value">${escapeHtml(item.value)}</div>
            </div>
        `)
        .join("");
}

function renderProviderRegistry() {
    const providers = Object.keys(settingsStore.PROVIDER_CATALOG);
    settingsRefs.providerGrid.innerHTML = providers
        .map((provider) => renderProviderCard(provider))
        .join("");
}

function renderLocalModelLab() {
    const local = settingsState.local_models || {};
    settingsRefs.ollamaModelInput.value = local.ollamaModel || "";
    settingsRefs.ollamaBaseUrlInput.value = local.ollamaBaseUrl || settingsState.secrets.ollamaBaseUrl || "";
    settingsRefs.ggufModelPathInput.value = local.modelFilePath || "";
    settingsRefs.hfReferenceInput.value = local.huggingFaceUrl || "";
    settingsRefs.hfDownloadPatternInput.value = local.hfDownloadPattern || "*.gguf";
    settingsRefs.hfLocalDirInput.value = local.hfLocalDir || "";
    settingsRefs.llamaCppCommandInput.value = local.llamaCppCommand || "llama-server";
    settingsRefs.llamaCppPortInput.value = String(local.llamaCppPort || 8001);
    settingsRefs.localEndpointUrlInput.value = local.localEndpointUrl || "";
    settingsRefs.localEndpointModelInput.value = local.localEndpointModel || "";
    settingsRefs.localEndpointApiKeyInput.value = local.localEndpointApiKey || "";
    settingsRefs.localEndpointAuthHeaderInput.value = local.localEndpointAuthHeader || "Authorization";

    settingsRefs.ollamaStatus.textContent =
        local.selectedLocalProvider === "ollama"
            ? `Selected for project routing · ${local.ollamaModel || "no model selected"}`
            : `Current Ollama model: ${local.ollamaModel || "not set"}`;

    settingsRefs.llamaCppStatus.textContent =
        local.llamaCppStatus === "ready"
            ? `llama.cpp server ready on port ${local.llamaCppPort} · PID ${local.llamaCppPid || "unknown"}`
            : local.llamaCppStatus === "starting"
                ? `llama.cpp server is starting on port ${local.llamaCppPort}`
                : "No llama.cpp runner active.";

    settingsRefs.localEndpointStatus.textContent =
        local.selectedLocalProvider === "custom"
            ? `Selected for project routing · ${local.localEndpointModel || "local-model"}`
            : `Saved endpoint: ${local.localEndpointUrl || "not set"}`;
}

function renderProviderCard(provider) {
    const meta = settingsStore.PROVIDER_CATALOG[provider];
    const state = getProviderStatus(provider);
    const saved = settingsState.providerRegistry.providers[provider] || {};
    const modelOptions = getModelOptionsForProvider(provider);
    const actionLabel = saved.enabled === false ? "Reconnect" : "Disconnect";
    const actionName = saved.enabled === false ? "reconnect" : "disconnect";

    return `
        <article class="provider-card">
            <div class="provider-card-head">
                <div class="provider-card-title">
                    <span class="provider-card-icon"><i data-lucide="${meta.icon}"></i></span>
                    <div>
                        <div class="provider-card-name">${escapeHtml(meta.label)}</div>
                        <div class="provider-card-sub">${escapeHtml(describeProvider(provider, meta))}</div>
                    </div>
                </div>
                <span class="provider-status status-${escapeHtml(state.status)}">
                    <span class="status-dot"></span>
                    ${escapeHtml(formatStatusLabel(state.status))}
                </span>
            </div>

            <div class="provider-meta">
                ${meta.badges.map((badge) => `<span class="meta-badge">${escapeHtml(badge)}</span>`).join("")}
                ${meta.supports.map((item) => `<span class="meta-badge">${escapeHtml(item)}</span>`).join("")}
            </div>

            <div class="provider-controls">
                <label class="settings-field">
                    <span>Selected default model</span>
                    <select data-provider-model-select="${escapeHtml(provider)}">
                        ${modelOptions.map((option) => `
                            <option value="${escapeHtml(option.value)}" ${option.value === saved.selectedModel ? "selected" : ""}>
                                ${escapeHtml(option.label)}
                            </option>
                        `).join("")}
                    </select>
                </label>
                <div class="provider-progress"><span style="width:${state.progress || 0}%"></span></div>
                <div class="provider-message">${escapeHtml(state.message)}</div>
            </div>

            <div class="provider-card-actions">
                <button class="btn ghost" data-provider-action="test" data-provider="${escapeHtml(provider)}">
                    <i data-lucide="plug"></i> Test
                </button>
                <button class="btn ghost" data-provider-action="refresh" data-provider="${escapeHtml(provider)}">
                    <i data-lucide="refresh-cw"></i> Refresh Models
                </button>
                <button class="btn ghost" data-provider-action="warm" data-provider="${escapeHtml(provider)}">
                    <i data-lucide="refresh-cw"></i> Warm Up
                </button>
                <button class="btn ghost" data-provider-action="${escapeHtml(actionName)}" data-provider="${escapeHtml(provider)}">
                    <i data-lucide="${saved.enabled === false ? "plug" : "power"}"></i> ${escapeHtml(actionLabel)}
                </button>
            </div>
        </article>
    `;
}

function describeProvider(provider, meta) {
    if (provider === "custom") return settingsState.local_models?.localEndpointUrl || "Local or custom OpenAI-compatible endpoint";
    const bootstrapProvider = bootstrapState?.providers?.[provider];
    if (provider === "ollama") {
        return `Local bridge at ${bootstrapProvider?.base_url || settingsState.secrets.ollamaBaseUrl}`;
    }
    return bootstrapProvider?.default_model || meta.type;
}

function formatStatusLabel(status) {
    return status.replace(/_/g, " ");
}

function getProviderStatus(provider) {
    const savedState = settingsState.providerRegistry.providerStates[provider] || {};
    const savedProvider = settingsState.providerRegistry.providers[provider] || {};

    if (savedProvider.enabled === false) {
        return {
            status: "disconnected",
            message: "Disabled locally. Reconnect to use it for defaults again.",
            progress: 0,
        };
    }

    if (savedState.status && ["authenticating", "fetching_models", "selecting_model", "warming_up", "in_use", "error"].includes(savedState.status)) {
        return savedState;
    }

    if (provider === "custom") {
        const localEndpointReady = (settingsState.local_models?.localEndpointUrl || "").trim();
        if (!settingsState.customEndpoints.length && !localEndpointReady) {
            return {
                status: "disconnected",
                message: "No custom endpoints saved yet.",
                progress: 0,
            };
        }
        return {
            status: savedState.status || "ready",
            message: savedState.message || (
                localEndpointReady
                    ? `Active runtime endpoint: ${settingsState.local_models.localEndpointModel || "local-model"}`
                    : `${settingsState.customEndpoints.length} custom endpoint(s) saved.`
            ),
            progress: savedState.progress || 100,
        };
    }

    if (provider === "auto") {
        const readyCount = Object.keys(settingsStore.PROVIDER_CATALOG)
            .filter((key) => key !== "auto" && key !== "custom")
            .filter((key) => {
                const state = settingsState.providerRegistry.providerStates[key];
                return state?.status === "ready" || bootstrapState?.providers?.[key]?.configured;
            }).length;

        return readyCount
            ? {
                status: "ready",
                message: `${readyCount} provider(s) available for auto fallback routing.`,
                progress: 100,
            }
            : {
                status: "key_missing",
                message: "No live provider is ready for automatic fallback yet.",
                progress: 0,
            };
    }

    const bootstrapProvider = bootstrapState?.providers?.[provider];
    if (provider === "ollama" && savedState.status === "ready") {
        return savedState;
    }

    if (savedState.status === "ready") return savedState;

    if (bootstrapProvider?.configured && provider !== "ollama") {
        return {
            status: "ready",
            message: `Configured on the backend${savedState.lastCheckedAt ? ` · checked ${formatTime(savedState.lastCheckedAt)}` : ""}.`,
            progress: 100,
        };
    }

    const localSecret = getLocalSecretValue(provider);
    if (localSecret) {
        return {
            status: "key_missing",
            message: "A runtime key or endpoint is saved locally. Test this provider to verify readiness.",
            progress: 0,
        };
    }

    if (provider === "ollama") {
        return {
            status: "disconnected",
            message: "Local bridge metadata is configured, but the bridge has not been tested yet.",
            progress: 0,
        };
    }

    return {
        status: "key_missing",
        message: "No backend configuration detected for this provider.",
        progress: 0,
    };
}

function getLocalSecretValue(provider) {
    if (provider === "gemini") return settingsState.secrets.geminiApiKey;
    if (provider === "groq") return settingsState.secrets.groqApiKey;
    if (provider === "groq_2") return settingsState.secrets.groqApiKey2;
    if (provider === "ollama") return settingsState.secrets.ollamaBaseUrl;
    if (provider === "custom") return settingsState.local_models.localEndpointUrl;
    return "";
}

function getSelectableProviders(includeCustom = true) {
    const options = settingsStore.buildProviderOptions(settingsState.customEndpoints)
        .filter((option) => includeCustom || !option.value.startsWith("custom:"));
    return options;
}

function getModelOptionsForProvider(provider) {
    const modelMap = bootstrapState?.models || {};
    if (provider === "auto") {
        return [{ value: "auto", label: modelMap.auto || "Auto" }];
    }
    if (provider === "gemini") {
        return [{ value: "gemini", label: modelMap.gemini || "Gemini" }];
    }
    if (provider === "groq") {
        return Object.entries(modelMap)
            .filter(([key]) => key.startsWith("groq"))
            .map(([key, label]) => ({ value: key, label }));
    }
    if (provider === "groq_2") {
        return [{ value: "groq_2", label: modelMap.groq_2 || "Groq Fallback" }];
    }
    if (provider === "ollama") {
        const active = bootstrapState?.providers?.ollama?.default_model || settingsState.local_models?.ollamaModel || "Ollama";
        return [{ value: "ollama", label: `Ollama (${active})` }];
    }
    if (provider === "custom") {
        const options = [];
        if ((settingsState.local_models?.localEndpointUrl || "").trim()) {
            options.push({
                value: "custom",
                label: settingsState.local_models.localEndpointModel || "Configured local endpoint",
            });
        }
        settingsState.customEndpoints.forEach((endpoint) => {
            options.push({
                value: endpoint.id,
                label: endpoint.modelId || endpoint.name || endpoint.endpointUrl,
            });
        });
        return options.length ? options : [{ value: "", label: "Add or configure an endpoint first" }];
    }
    if (provider.startsWith("custom:")) {
        const endpoint = settingsState.customEndpoints.find((item) => item.id === provider.split(":")[1]);
        return endpoint ? [{ value: provider, label: endpoint.modelId || endpoint.name || endpoint.endpointUrl }] : [];
    }
    return [{ value: provider, label: provider }];
}

function handleProviderChange(event) {
    const provider = event.target.dataset.providerModelSelect;
    if (!provider) return;
    settingsStore.updateSettings((draft) => {
        draft.providerRegistry.providers[provider].selectedModel = event.target.value;
        if (provider === "custom") {
            const endpoint = draft.customEndpoints.find((item) => item.id === event.target.value);
            if (endpoint) {
                draft.local_models.localEndpointUrl = endpoint.endpointUrl;
                draft.local_models.localEndpointModel = endpoint.modelId || endpoint.name || "local-model";
                draft.local_models.localEndpointApiKey = endpoint.apiKey || "";
                draft.local_models.localEndpointAuthHeader = endpoint.authHeader || "Authorization";
            }
        }
    });
    settingsStore.appendLog({
        type: "provider_model",
        message: `Updated ${provider} default model to ${event.target.value}`,
    });
    persistRuntimeSettings(`Updated ${provider} selected model`);
    renderAll();
}

async function handleProviderAction(event) {
    const action = event.target.closest("[data-provider-action]")?.dataset.providerAction;
    const provider = event.target.closest("[data-provider-action]")?.dataset.provider;
    if (!action || !provider) return;

    if (action === "refresh") {
        await refreshBootstrap(true);
        return;
    }

    if (action === "disconnect" || action === "reconnect") {
        settingsStore.updateSettings((draft) => {
            draft.providerRegistry.providers[provider].enabled = action === "reconnect";
            draft.providerRegistry.providerStates[provider] = {
                status: action === "reconnect" ? "disconnected" : "disconnected",
                message: action === "reconnect"
                    ? "Reconnected locally. Test or warm up to verify readiness."
                    : "Disconnected locally from advanced defaults.",
                progress: 0,
                lastCheckedAt: new Date().toISOString(),
            };
        });
        settingsStore.appendLog({
            type: "provider_toggle",
            message: `${action === "reconnect" ? "Reconnected" : "Disconnected"} ${provider} locally`,
        });
        persistRuntimeSettings(`${action === "reconnect" ? "Reconnected" : "Disconnected"} ${provider}`);
        renderAll();
        return;
    }

    if (action === "test" || action === "warm") {
        await runProviderLifecycle(provider, action === "warm");
    }
}

async function runProviderLifecycle(provider, warmUp) {
    const stages = [
        { status: "authenticating", progress: 18, message: "Validating configuration…" },
        { status: "fetching_models", progress: 46, message: "Refreshing model metadata…" },
        { status: "selecting_model", progress: 70, message: "Selecting active model…" },
    ];

    if (warmUp) {
        stages.push({ status: "warming_up", progress: 88, message: "Warming provider…" });
    }

    settingsStore.updateSettings((draft) => {
        draft.providerRegistry.providers[provider].enabled = true;
    });

    for (const stage of stages) {
        updateProviderState(provider, stage);
        renderAll();
        await delay(220);
    }

    await persistRuntimeSettings();

    try {
        const res = await apiFetch("/settings/test_provider", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider }),
        });
        const data = await res.json();
        updateProviderState(provider, {
            status: data.status || "ready",
            progress: data.status === "ready" ? 100 : 0,
            message: data.message || "Provider test complete.",
            lastCheckedAt: new Date().toISOString(),
            resolvedProvider: data.resolved_provider || provider,
        });
        settingsStore.appendLog({
            type: "provider_test",
            message: `${provider}: ${data.message || data.status}`,
        });
    } catch (e) {
        updateProviderState(provider, {
            status: "error",
            progress: 0,
            message: String(e),
            lastCheckedAt: new Date().toISOString(),
        });
        settingsStore.appendLog({
            type: "provider_test_error",
            message: `${provider}: ${e}`,
        });
    }
    renderAll();
}

async function listOllamaModels() {
    settingsRefs.ollamaStatus.textContent = "Checking local Ollama models…";
    try {
        const res = await apiFetch("/settings/local_models/ollama_list");
        const data = await res.json();
        if (data.status !== "ready") {
            settingsRefs.ollamaStatus.textContent = data.message || "Unable to list Ollama models.";
            return;
        }
        settingsRefs.ollamaList.innerHTML = data.models.length
            ? data.models.map((model) => `
                <div class="endpoint-item">
                    <div>
                        <div class="endpoint-item-name">${escapeHtml(model.name)}</div>
                        <div class="endpoint-item-sub">${escapeHtml(model.raw)}</div>
                    </div>
                    <div class="endpoint-item-actions">
                        <button class="btn ghost" data-ollama-use="${escapeHtml(model.name)}">
                            <i data-lucide="box"></i> Use
                        </button>
                    </div>
                </div>
            `).join("")
            : `<div class="empty-state">No local Ollama models detected.</div>`;
        settingsRefs.ollamaStatus.textContent = `${data.models.length} local Ollama model(s) found.`;
        lucide.createIcons();
    } catch (e) {
        settingsRefs.ollamaStatus.textContent = String(e);
    }
}

async function handleOllamaListAction(event) {
    const btn = event.target.closest("[data-ollama-use]");
    if (!btn) return;
    settingsRefs.ollamaModelInput.value = btn.dataset.ollamaUse;
    await useOllamaModelForProject();
}

async function pullOllamaModel() {
    const modelRef = settingsRefs.ollamaModelInput.value.trim();
    if (!modelRef) {
        settingsRefs.ollamaStatus.textContent = "Enter an Ollama model tag or import ref first.";
        return;
    }
    settingsRefs.ollamaStatus.textContent = `Pulling ${modelRef}…`;
    syncLocalModelDraft();
    try {
        const res = await apiFetch("/settings/local_models/ollama_pull", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model_ref: modelRef }),
        });
        const data = await res.json();
        settingsRefs.ollamaStatus.textContent = data.message || data.status;
        settingsStore.appendLog({
            type: "ollama_pull",
            message: `${modelRef}: ${data.message || data.status}`,
        });
        await refreshRuntimeSettings(false);
        await refreshBootstrap(false);
        renderAll();
    } catch (e) {
        settingsRefs.ollamaStatus.textContent = String(e);
    }
}

async function useOllamaModelForProject() {
    syncLocalModelDraft();
    settingsStore.updateSettings((draft) => {
        draft.providerRegistry.defaultProvider = "ollama";
        draft.workflowDefaults.defaultProvider = "ollama";
        draft.local_models.selectedLocalProvider = "ollama";
        draft.secrets.ollamaBaseUrl = draft.local_models.ollamaBaseUrl;
    });
    await persistRuntimeSettings("Selected Ollama as the project-wide runtime provider");
    settingsStore.appendLog({
        type: "local_model_selection",
        message: `Selected Ollama model ${settingsRefs.ollamaModelInput.value.trim() || "not set"} for the project`,
    });
    renderAll();
}

async function downloadHuggingFaceModel() {
    syncLocalModelDraft();
    const repoOrUrl = settingsRefs.hfReferenceInput.value.trim();
    if (!repoOrUrl) {
        settingsRefs.llamaCppStatus.textContent = "Enter a Hugging Face repo id or model file URL first.";
        return;
    }
    settingsRefs.llamaCppStatus.textContent = "Downloading GGUF from Hugging Face…";
    try {
        const res = await apiFetch("/settings/local_models/hf_download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repo_or_url: repoOrUrl,
                include_pattern: settingsRefs.hfDownloadPatternInput.value.trim() || "*.gguf",
                local_dir: settingsRefs.hfLocalDirInput.value.trim(),
            }),
        });
        const data = await res.json();
        settingsRefs.llamaCppStatus.textContent = data.message || data.status;
        if (data.model_path) {
            settingsRefs.ggufModelPathInput.value = data.model_path;
            syncLocalModelDraft();
        }
        settingsStore.appendLog({
            type: "hf_download",
            message: `${repoOrUrl}: ${data.message || data.status}`,
        });
        await refreshRuntimeSettings(false);
        renderAll();
    } catch (e) {
        settingsRefs.llamaCppStatus.textContent = String(e);
    }
}

async function launchLlamaCppRunner() {
    syncLocalModelDraft();
    const modelPath = settingsRefs.ggufModelPathInput.value.trim();
    if (!modelPath) {
        settingsRefs.llamaCppStatus.textContent = "Enter a local GGUF model file path first.";
        return;
    }
    settingsRefs.llamaCppStatus.textContent = "Launching llama.cpp server…";
    try {
        const res = await apiFetch("/settings/local_models/launch_llama_cpp", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                model_path: modelPath,
                huggingface_url: settingsRefs.hfReferenceInput.value.trim(),
                port: Number(settingsRefs.llamaCppPortInput.value) || 8001,
                context_size: 4096,
                command: settingsRefs.llamaCppCommandInput.value.trim() || "llama-server",
            }),
        });
        const data = await res.json();
        settingsRefs.llamaCppStatus.textContent = data.message || data.status;
        settingsStore.appendLog({
            type: "llama_cpp_launch",
            message: `${modelPath}: ${data.message || data.status}`,
        });
        await refreshRuntimeSettings(false);
        await refreshBootstrap(false);
        renderAll();
    } catch (e) {
        settingsRefs.llamaCppStatus.textContent = String(e);
    }
}

async function stopLlamaCppRunner() {
    settingsRefs.llamaCppStatus.textContent = "Stopping llama.cpp server…";
    try {
        const res = await apiFetch("/settings/local_models/stop_llama_cpp", { method: "POST" });
        const data = await res.json();
        settingsRefs.llamaCppStatus.textContent = data.message || data.status;
        settingsStore.appendLog({
            type: "llama_cpp_stop",
            message: data.message || data.status,
        });
        await refreshRuntimeSettings(false);
        renderAll();
    } catch (e) {
        settingsRefs.llamaCppStatus.textContent = String(e);
    }
}

async function testSavedLocalEndpoint() {
    syncLocalModelDraft();
    settingsRefs.localEndpointStatus.textContent = "Testing local endpoint…";
    try {
        const res = await apiFetch("/settings/test_custom_endpoint", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                endpoint_url: settingsRefs.localEndpointUrlInput.value.trim(),
                model: settingsRefs.localEndpointModelInput.value.trim() || "local-model",
                api_key: settingsRefs.localEndpointApiKeyInput.value.trim(),
                auth_header: settingsRefs.localEndpointAuthHeaderInput.value.trim() || "Authorization",
            }),
        });
        const data = await res.json();
        settingsRefs.localEndpointStatus.textContent = data.message || data.status;
        settingsStore.updateSettings((draft) => {
            draft.providerRegistry.providerStates.custom = {
                status: data.status || "error",
                progress: data.status === "ready" ? 100 : 0,
                message: data.message || "",
                lastCheckedAt: new Date().toISOString(),
            };
        });
        await persistRuntimeSettings("Tested local custom endpoint");
        settingsStore.appendLog({
            type: "local_endpoint_test",
            message: data.message || data.status,
        });
        renderAll();
    } catch (e) {
        settingsRefs.localEndpointStatus.textContent = String(e);
    }
}

async function useLocalEndpointForProject() {
    syncLocalModelDraft();
    settingsStore.updateSettings((draft) => {
        draft.providerRegistry.defaultProvider = "custom";
        draft.workflowDefaults.defaultProvider = "custom";
        draft.local_models.selectedLocalProvider = "custom";
    });
    await persistRuntimeSettings("Selected custom local endpoint as the project-wide runtime provider");
    settingsStore.appendLog({
        type: "local_model_selection",
        message: `Selected custom endpoint ${settingsRefs.localEndpointUrlInput.value.trim()} for the project`,
    });
    renderAll();
}

function syncLocalModelDraft() {
    settingsStore.updateSettings((draft) => {
        draft.local_models.ollamaModel = settingsRefs.ollamaModelInput.value.trim();
        draft.local_models.ollamaBaseUrl = settingsRefs.ollamaBaseUrlInput.value.trim() || "http://localhost:11434";
        draft.local_models.modelFilePath = settingsRefs.ggufModelPathInput.value.trim();
        draft.local_models.huggingFaceUrl = settingsRefs.hfReferenceInput.value.trim();
        draft.local_models.hfDownloadPattern = settingsRefs.hfDownloadPatternInput.value.trim() || "*.gguf";
        draft.local_models.hfLocalDir = settingsRefs.hfLocalDirInput.value.trim();
        draft.local_models.llamaCppCommand = settingsRefs.llamaCppCommandInput.value.trim() || "llama-server";
        draft.local_models.llamaCppPort = Number(settingsRefs.llamaCppPortInput.value) || 8001;
        draft.local_models.localEndpointUrl = settingsRefs.localEndpointUrlInput.value.trim();
        draft.local_models.localEndpointModel = settingsRefs.localEndpointModelInput.value.trim() || "local-model";
        draft.local_models.localEndpointApiKey = settingsRefs.localEndpointApiKeyInput.value.trim();
        draft.local_models.localEndpointAuthHeader = settingsRefs.localEndpointAuthHeaderInput.value.trim() || "Authorization";
        draft.secrets.ollamaBaseUrl = draft.local_models.ollamaBaseUrl;
    });
    settingsState = settingsStore.loadSettings();
}

function updateProviderState(provider, patch) {
    settingsStore.updateSettings((draft) => {
        draft.providerRegistry.providerStates[provider] = {
            ...(draft.providerRegistry.providerStates[provider] || {}),
            ...patch,
        };
    });
    settingsState = settingsStore.loadSettings();
    persistRuntimeSettings(`Updated ${provider} provider state`);
}

function renderRoleAssignments() {
    const providerOptions = getSelectableProviders(true);
    settingsRefs.roleAssignments.innerHTML = settingsStore.ROLE_ORDER
        .map((role) => {
            const assignment = settingsState.providerRegistry.roleAssignments[role.id];
            const modelOptions = getModelOptionsForProvider(assignment.provider);
            return `
                <div class="role-row">
                    <div>
                        <div class="role-label">${escapeHtml(role.label)}</div>
                        <div class="role-sub">Stored orchestration default</div>
                    </div>
                    <label class="settings-field">
                        <span>Provider</span>
                        <select data-role-provider="${escapeHtml(role.id)}">
                            ${providerOptions.map((option) => `
                                <option value="${escapeHtml(option.value)}" ${option.value === assignment.provider ? "selected" : ""}>
                                    ${escapeHtml(option.label)}
                                </option>
                            `).join("")}
                        </select>
                    </label>
                    <label class="settings-field">
                        <span>Model</span>
                        <select data-role-model="${escapeHtml(role.id)}">
                            ${modelOptions.map((option) => `
                                <option value="${escapeHtml(option.value)}" ${option.value === assignment.model ? "selected" : ""}>
                                    ${escapeHtml(option.label)}
                                </option>
                            `).join("")}
                        </select>
                    </label>
                </div>
            `;
        })
        .join("");
}

function handleRoleAssignmentChange(event) {
    const providerRole = event.target.dataset.roleProvider;
    const modelRole = event.target.dataset.roleModel;
    if (!providerRole && !modelRole) return;

    settingsStore.updateSettings((draft) => {
        if (providerRole) {
            draft.providerRegistry.roleAssignments[providerRole].provider = event.target.value;
            const modelOptions = getModelOptionsForProvider(event.target.value);
            draft.providerRegistry.roleAssignments[providerRole].model = modelOptions[0]?.value || "";
        }
        if (modelRole) {
            draft.providerRegistry.roleAssignments[modelRole].model = event.target.value;
        }
    });

    settingsStore.appendLog({
        type: "role_assignment",
        message: providerRole
            ? `Updated ${providerRole} role provider to ${event.target.value}`
            : `Updated ${modelRole} role model to ${event.target.value}`,
    });
    persistRuntimeSettings("Updated per-role model assignments");
    renderAll();
}

function renderSecrets() {
    settingsRefs.secretsGrid.innerHTML = SECRET_FIELDS
        .map((field) => {
            const value = settingsState.secrets[field.id] || "";
            const testSupported = field.provider !== "custom" || !!value;
            return `
                <div class="secret-row">
                    <div class="secret-meta">
                        <div class="secret-title">${escapeHtml(field.label)}</div>
                        <div class="secret-sub">${escapeHtml(field.description)}</div>
                    </div>
                    <input
                        type="${escapeHtml(field.type)}"
                        data-secret-input="${escapeHtml(field.id)}"
                        placeholder="${escapeHtml(field.placeholder)}"
                        value="${escapeHtml(value)}"
                    >
                    <div class="secret-actions">
                        <button class="btn ghost" data-secret-action="toggle" data-secret="${escapeHtml(field.id)}">
                            <i data-lucide="eye"></i> Show
                        </button>
                        <button class="btn ghost" data-secret-action="test" data-secret="${escapeHtml(field.id)}" ${testSupported ? "" : "disabled"}>
                            <i data-lucide="plug"></i> Test
                        </button>
                        <button class="btn ghost" data-secret-action="clear" data-secret="${escapeHtml(field.id)}">
                            <i data-lucide="trash-2"></i> Clear
                        </button>
                    </div>
                </div>
            `;
        })
        .join("");
}

function handleSecretChange(event) {
    const secretId = event.target.dataset.secretInput;
    if (!secretId) return;
    settingsStore.updateSettings((draft) => {
        draft.secrets[secretId] = event.target.value;
        if (secretId === "ollamaBaseUrl") {
            draft.local_models.ollamaBaseUrl = event.target.value;
        }
    });
    settingsStore.appendLog({
        type: "secret_saved",
        message: `Updated ${secretId} (${settingsStore.maskSecret(event.target.value)})`,
    });
    persistRuntimeSettings(`Updated ${secretId}`);
}

async function handleSecretAction(event) {
    const btn = event.target.closest("[data-secret-action]");
    if (!btn) return;
    const action = btn.dataset.secretAction;
    const secretId = btn.dataset.secret;
    const input = settingsRefs.secretsGrid.querySelector(`[data-secret-input="${secretId}"]`);
    if (!input) return;

    if (action === "toggle") {
        input.type = input.type === "password" ? "text" : SECRET_FIELDS.find((field) => field.id === secretId)?.type || "password";
        btn.innerHTML = input.type === "password"
            ? `<i data-lucide="eye"></i> Show`
            : `<i data-lucide="eye-off"></i> Hide`;
        lucide.createIcons();
        return;
    }

    if (action === "clear") {
        input.value = "";
        settingsStore.updateSettings((draft) => {
            draft.secrets[secretId] = "";
            if (secretId === "ollamaBaseUrl") {
                draft.local_models.ollamaBaseUrl = "";
            }
        });
        settingsStore.appendLog({
            type: "secret_cleared",
            message: `Cleared ${secretId}`,
        });
        persistRuntimeSettings(`Cleared ${secretId}`);
        renderSecrets();
        lucide.createIcons();
        return;
    }

    if (action === "test") {
        const field = SECRET_FIELDS.find((item) => item.id === secretId);
        if (!field) return;
        settingsStore.updateSettings((draft) => {
            draft.secrets[secretId] = input.value;
            if (secretId === "ollamaBaseUrl") {
                draft.local_models.ollamaBaseUrl = input.value;
            }
        });
        await persistRuntimeSettings(`Updated ${secretId} before provider test`);
        if (field.provider === "custom") {
            settingsStore.appendLog({
                type: "secret_test",
                message: `${secretId}: token saved for Hugging Face/custom endpoint workflows (${settingsStore.maskSecret(input.value)})`,
            });
            renderLogs();
            return;
        }
        await runProviderLifecycle(field.provider, false);
    }
}

function renderEndpoints() {
    const endpoints = settingsState.customEndpoints;
    settingsRefs.endpointList.innerHTML = endpoints.length
        ? endpoints.map((endpoint) => `
            <div class="endpoint-item">
                <div>
                    <div class="endpoint-item-name">${escapeHtml(endpoint.name || endpoint.modelId || endpoint.endpointUrl)}</div>
                    <div class="endpoint-item-sub">
                        ${escapeHtml(endpoint.endpointUrl)} · ${escapeHtml(endpoint.modelId || "model pending")} · ${escapeHtml(endpoint.type)}
                    </div>
                    <div class="provider-meta" style="margin-top:0.45rem">
                        ${endpoint.streaming ? `<span class="meta-badge">streaming</span>` : ""}
                        ${endpoint.toolUse ? `<span class="meta-badge">tool-use</span>` : ""}
                        ${endpoint.jsonMode ? `<span class="meta-badge">json</span>` : ""}
                        ${endpoint.lastTestStatus ? `<span class="meta-badge">${escapeHtml(endpoint.lastTestStatus)}</span>` : ""}
                    </div>
                </div>
                <div class="endpoint-item-actions">
                    <button class="btn ghost" data-endpoint-action="test" data-endpoint-id="${escapeHtml(endpoint.id)}">
                        <i data-lucide="plug"></i> Test
                    </button>
                    <button class="btn ghost" data-endpoint-action="make-default" data-endpoint-id="${escapeHtml(endpoint.id)}">
                        <i data-lucide="star"></i> Use in Roles
                    </button>
                    <button class="btn ghost" data-endpoint-action="remove" data-endpoint-id="${escapeHtml(endpoint.id)}">
                        <i data-lucide="trash-2"></i> Remove
                    </button>
                </div>
            </div>
        `).join("")
        : `<div class="empty-state">No custom endpoints saved yet.</div>`;
}

async function handleEndpointSubmit(event) {
    event.preventDefault();
    const endpoint = readDraftEndpoint();
    if (!endpoint.name && !endpoint.modelId) {
        alert("Add at least a display name or model identifier.");
        return;
    }
    settingsStore.updateSettings((draft) => {
        draft.customEndpoints.push(endpoint);
    });
    settingsStore.appendLog({
        type: "custom_endpoint",
        message: `Saved custom endpoint ${endpoint.name || endpoint.modelId || endpoint.endpointUrl}`,
    });
    persistRuntimeSettings(`Saved custom endpoint ${endpoint.name || endpoint.modelId || endpoint.endpointUrl}`);
    settingsRefs.endpointForm.reset();
    settingsRefs.endpointAuthHeader.value = "Authorization";
    settingsRefs.endpointStreaming.checked = true;
    settingsRefs.endpointJsonMode.checked = true;
    renderAll();
}

function readDraftEndpoint() {
    return {
        id: "endpoint_" + Math.random().toString(36).slice(2, 10),
        name: settingsRefs.endpointName.value.trim(),
        endpointUrl: settingsRefs.endpointUrl.value.trim(),
        modelId: settingsRefs.endpointModelId.value.trim(),
        authHeader: settingsRefs.endpointAuthHeader.value.trim() || "Authorization",
        apiKey: settingsRefs.endpointApiKey.value.trim(),
        type: settingsRefs.endpointType.value,
        streaming: settingsRefs.endpointStreaming.checked,
        toolUse: settingsRefs.endpointToolUse.checked,
        jsonMode: settingsRefs.endpointJsonMode.checked,
        lastTestStatus: "",
        lastTestAt: "",
    };
}

async function testDraftEndpoint() {
    const draft = readDraftEndpoint();
    if (!draft.endpointUrl) {
        alert("Enter an endpoint URL first.");
        return;
    }
    const result = await testEndpointViaServer(draft);
    settingsStore.appendLog({
        type: "endpoint_test",
        message: `${draft.endpointUrl}: ${result.message}`,
    });
    renderLogs();
}

async function handleEndpointListAction(event) {
    const btn = event.target.closest("[data-endpoint-action]");
    if (!btn) return;
    const action = btn.dataset.endpointAction;
    const endpointId = btn.dataset.endpointId;
    const endpoint = settingsState.customEndpoints.find((item) => item.id === endpointId);
    if (!endpoint) return;

    if (action === "remove") {
        settingsStore.updateSettings((draft) => {
            draft.customEndpoints = draft.customEndpoints.filter((item) => item.id !== endpointId);
        });
        settingsStore.appendLog({
            type: "custom_endpoint_removed",
            message: `Removed custom endpoint ${endpoint.name || endpoint.modelId || endpoint.endpointUrl}`,
        });
        persistRuntimeSettings(`Removed custom endpoint ${endpoint.name || endpoint.modelId || endpoint.endpointUrl}`);
        renderAll();
        return;
    }

    if (action === "make-default") {
        settingsStore.updateSettings((draft) => {
            draft.providerRegistry.roleAssignments.coder = {
                provider: `custom:${endpointId}`,
                model: `custom:${endpointId}`,
            };
            draft.providerRegistry.roleAssignments.executor = {
                provider: `custom:${endpointId}`,
                model: `custom:${endpointId}`,
            };
            draft.providerRegistry.defaultProvider = "custom";
            draft.workflowDefaults.defaultProvider = "custom";
            draft.local_models.selectedLocalProvider = "custom";
            draft.local_models.localEndpointUrl = endpoint.endpointUrl;
            draft.local_models.localEndpointModel = endpoint.modelId || endpoint.name || "local-model";
            draft.local_models.localEndpointApiKey = endpoint.apiKey || "";
            draft.local_models.localEndpointAuthHeader = endpoint.authHeader || "Authorization";
        });
        settingsStore.appendLog({
            type: "custom_endpoint_role_default",
            message: `Assigned ${endpoint.name || endpoint.modelId || endpoint.endpointUrl} to coder and executor roles`,
        });
        persistRuntimeSettings(`Set ${endpoint.name || endpoint.modelId || endpoint.endpointUrl} as custom runtime endpoint`);
        renderAll();
        return;
    }

    if (action === "test") {
        const result = await testEndpointViaServer(endpoint);
        settingsStore.updateSettings((draft) => {
            const target = draft.customEndpoints.find((item) => item.id === endpointId);
            if (target) {
                target.lastTestStatus = result.status;
                target.lastTestAt = new Date().toISOString();
            }
            draft.providerRegistry.providerStates.custom = {
                status: result.status === "ready" ? "ready" : "error",
                progress: result.status === "ready" ? 100 : 0,
                message: result.message,
                lastCheckedAt: new Date().toISOString(),
            };
        });
        settingsStore.appendLog({
            type: "custom_endpoint_test",
            message: `${endpoint.name || endpoint.endpointUrl}: ${result.message}`,
        });
        persistRuntimeSettings(`Updated custom endpoint test status for ${endpoint.name || endpoint.endpointUrl}`);
        renderAll();
    }
}

async function testEndpointViaServer(endpoint) {
    try {
        const res = await apiFetch("/settings/test_custom_endpoint", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                endpoint_url: endpoint.endpointUrl,
                model: endpoint.modelId || endpoint.name || "local-model",
                api_key: endpoint.apiKey || "",
                auth_header: endpoint.authHeader || "Authorization",
            }),
        });
        return await res.json();
    } catch (e) {
        return {
            status: "error",
            message: `Endpoint test failed: ${e}`,
        };
    }
}

function renderLogs() {
    const logs = settingsStore.getLogs();
    settingsRefs.logList.innerHTML = logs.length
        ? logs.map((log) => `
            <div class="log-item">
                <div class="log-item-head">
                    <span class="log-item-type">${escapeHtml(log.type || "log")}</span>
                    <span class="log-item-time">${escapeHtml(formatTime(log.timestamp))}</span>
                </div>
                <div class="log-item-body">${escapeHtml(log.message || JSON.stringify(log, null, 2))}</div>
            </div>
        `).join("")
        : `<div class="empty-state">No runtime logs yet.</div>`;
}

function restoreDefaults() {
    settingsStore.saveSettings(settingsStore.createDefaults());
    settingsStore.appendLog({
        type: "settings_restore",
        message: "Restored advanced settings to defaults",
    });
    settingsState = settingsStore.loadSettings();
    persistRuntimeSettings("Restored runtime settings to defaults");
    renderAll();
}

function formatTime(iso) {
    if (!iso) return "Never";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "Unknown";
    return date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
    });
}

function escapeHtml(value) {
    return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

initSettingsPage();

// Settings tab shell: keeps the advanced page organized without re-rendering each panel.
(function setupSettingsTabs() {
    const validTabs = new Set(["overview", "providers", "local", "keys", "endpoints", "logs"]);
    const sectionToTab = {
        workflowDefaults: "overview", systemSummary: "overview", providerRegistry: "providers",
        roleAssignmentsSection: "providers", localModelLab: "local", secretsSection: "keys",
        customEndpointSection: "endpoints", runtimeLogs: "logs",
    };
    function resolveTab(raw) {
        const value = (raw || "").replace("#", "");
        return sectionToTab[value] || (validTabs.has(value) ? value : "overview");
    }
    function setTab(tab, updateHash = true) {
        const active = resolveTab(tab);
        document.querySelectorAll("[data-settings-tab]").forEach((button) => {
            button.classList.toggle("active", button.dataset.settingsTab === active);
        });
        document.querySelectorAll("[data-settings-panel]").forEach((panel) => {
            panel.classList.toggle("active", panel.dataset.settingsPanel === active);
        });
        if (updateHash) history.replaceState(null, "", `#${active}`);
    }
    document.querySelectorAll("[data-settings-tab]").forEach((button) => {
        button.addEventListener("click", () => setTab(button.dataset.settingsTab));
    });
    window.addEventListener("hashchange", () => setTab(window.location.hash, false));
    setTab(window.location.hash, false);
})();

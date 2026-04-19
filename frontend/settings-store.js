(function (window) {
    const SETTINGS_KEY = "autodev-project-settings-v1";
    const LOG_KEY = "autodev-runtime-logs-v1";

    const PROVIDER_CATALOG = {
        auto: {
            label: "Auto",
            icon: "sparkles",
            type: "remote",
            auth: "server-env",
            supports: ["routing", "fallbacks", "streaming"],
            badges: ["orchestrator", "recommended"],
        },
        gemini: {
            label: "Gemini",
            icon: "sparkles",
            type: "remote",
            auth: "api-key",
            supports: ["reasoning", "tool-use", "streaming", "json"],
            badges: ["planner", "reviewer"],
        },
        groq: {
            label: "Groq",
            icon: "zap",
            type: "remote",
            auth: "api-key",
            supports: ["fast", "coding", "streaming", "json"],
            badges: ["coder", "executor"],
        },
        groq_2: {
            label: "Groq Fallback",
            icon: "zap",
            type: "remote",
            auth: "api-key",
            supports: ["fast", "fallback", "streaming", "json"],
            badges: ["fallback"],
        },
        ollama: {
            label: "Ollama",
            icon: "box",
            type: "local",
            auth: "bridge",
            supports: ["coding", "local-endpoint", "streaming"],
            badges: ["local"],
        },
        custom: {
            label: "Custom Endpoint",
            icon: "plug",
            type: "custom",
            auth: "custom",
            supports: ["custom-endpoint", "json", "streaming"],
            badges: ["advanced"],
        },
    };

    const ROLE_ORDER = [
        { id: "router", label: "Router" },
        { id: "planner", label: "Planner" },
        { id: "coder", label: "Coder" },
        { id: "reviewer", label: "Reviewer" },
        { id: "executor", label: "Executor / Fixer" },
    ];

    const WORKFLOW_PRESETS = [
        {
            id: "fast",
            label: "Fast",
            description: "Lean patch flow with fewer retries.",
            mode: "fast",
            maxRetries: 2,
        },
        {
            id: "balanced",
            label: "Balanced",
            description: "Default routing with moderate retries.",
            mode: "auto",
            maxRetries: 4,
        },
        {
            id: "deep",
            label: "Deep",
            description: "Full plan flow with the deepest repair loop.",
            mode: "plan",
            maxRetries: 6,
        },
    ];

    function createDefaults() {
        return {
            version: 2,
            updatedAt: null,
            workflowDefaults: {
                defaultProvider: "auto",
                workflowDepth: "balanced",
                mode: "auto",
                refinePrompt: true,
                maxRetries: 6,
                expertMode: false,
                skipSpec: false,
                skipReview: false,
            },
            providerRegistry: {
                defaultProvider: "auto",
                providers: {
                    auto: { enabled: true, selectedModel: "auto" },
                    gemini: { enabled: true, selectedModel: "gemini" },
                    groq: { enabled: true, selectedModel: "groq" },
                    groq_2: { enabled: true, selectedModel: "groq_2" },
                    ollama: { enabled: true, selectedModel: "ollama" },
                    custom: { enabled: true, selectedModel: "" },
                },
                providerStates: {},
                roleAssignments: {
                    router: { provider: "auto", model: "auto" },
                    planner: { provider: "gemini", model: "gemini" },
                    coder: { provider: "groq", model: "groq" },
                    reviewer: { provider: "auto", model: "auto" },
                    executor: { provider: "groq", model: "groq" },
                },
            },
            secrets: {
                geminiApiKey: "",
                groqApiKey: "",
                groqApiKey2: "",
                huggingFaceApiKey: "",
                ollamaBaseUrl: "http://localhost:11434",
            },
            local_models: {
                sourceType: "ollama",
                ollamaModel: "qwen2.5-coder:3b",
                ollamaBaseUrl: "http://localhost:11434",
                huggingFaceUrl: "",
                modelFilePath: "",
                llamaCppCommand: "llama-server",
                llamaCppPort: 8001,
                llamaCppContext: 4096,
                llamaCppPid: null,
                llamaCppStatus: "stopped",
                hfDownloadPattern: "*.gguf",
                hfLocalDir: "",
                localEndpointUrl: ""                                         ,
                localEndpointModel: "local-model",
                localEndpointApiKey: "",
                localEndpointAuthHeader: "Authorization",
                selectedLocalProvider: "ollama",
            },
            customEndpoints: [],
        };
    }

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function isObject(value) {
        return value && typeof value === "object" && !Array.isArray(value);
    }

    function deepMerge(base, override) {
        if (!isObject(base) || !isObject(override)) return clone(override);
        const out = clone(base);
        Object.entries(override).forEach(([key, value]) => {
            if (Array.isArray(value)) out[key] = clone(value);
            else if (isObject(value) && isObject(out[key])) out[key] = deepMerge(out[key], value);
            else out[key] = clone(value);
        });
        return out;
    }

    function runtimeToUiSettings(settings) {
        const source = clone(settings || {});
        const ui = clone(source);

        if (isObject(source.workflow_defaults) || isObject(source.workflowDefaults)) {
            ui.workflowDefaults = deepMerge(source.workflow_defaults || {}, source.workflowDefaults || {});
        }
        if (isObject(source.provider_registry) || isObject(source.providerRegistry)) {
            ui.providerRegistry = deepMerge(source.provider_registry || {}, source.providerRegistry || {});
        }
        if (isObject(source.localModels) || isObject(source.local_models)) {
            ui.local_models = deepMerge(source.local_models || {}, source.localModels || {});
        }

        delete ui.workflow_defaults;
        delete ui.provider_registry;
        delete ui.localModels;
        return ui;
    }

    function toRuntimeSettings(settings) {
        const ui = normalizeSettings(settings);
        const runtime = clone(ui);
        runtime.workflow_defaults = clone(ui.workflowDefaults);
        runtime.provider_registry = clone(ui.providerRegistry);
        delete runtime.workflowDefaults;
        delete runtime.providerRegistry;
        return runtime;
    }

    function stripSecretsFromRuntime(settings) {
        const runtime = toRuntimeSettings(settings);
        runtime.secrets = runtime.secrets || {};
        runtime.secrets.geminiApiKey = "";
        runtime.secrets.groqApiKey = "";
        runtime.secrets.groqApiKey2 = "";
        runtime.secrets.huggingFaceApiKey = "";
        runtime.local_models = runtime.local_models || {};
        runtime.local_models.localEndpointApiKey = "";
        if (Array.isArray(runtime.customEndpoints)) {
            runtime.customEndpoints = runtime.customEndpoints.map((endpoint) => ({
                ...endpoint,
                apiKey: "",
            }));
        }
        return runtime;
    }

    function mergeServerSettings(serverSettings, clientSettings) {
        const client = normalizeSettings(clientSettings || loadSettings());
        const merged = normalizeSettings(serverSettings || {});

        merged.secrets = {
            ...(merged.secrets || {}),
            geminiApiKey: client.secrets?.geminiApiKey || "",
            groqApiKey: client.secrets?.groqApiKey || "",
            groqApiKey2: client.secrets?.groqApiKey2 || "",
            huggingFaceApiKey: client.secrets?.huggingFaceApiKey || "",
        };
        merged.local_models = {
            ...(merged.local_models || {}),
            localEndpointApiKey: client.local_models?.localEndpointApiKey || "",
        };

        if (Array.isArray(merged.customEndpoints) && Array.isArray(client.customEndpoints)) {
            merged.customEndpoints = merged.customEndpoints.map((endpoint) => {
                const localMatch = client.customEndpoints.find((item) => item.id === endpoint.id);
                return localMatch ? { ...endpoint, apiKey: localMatch.apiKey || "" } : endpoint;
            });
        }

        return saveSettings(deepMerge(client, merged));
    }

    function encodeRuntimeSettings(settings) {
        try {
            const json = JSON.stringify(toRuntimeSettings(settings || loadSettings()));
            return btoa(unescape(encodeURIComponent(json)))
                .replace(/\+/g, "-")
                .replace(/\//g, "_")
                .replace(/=+$/g, "");
        } catch (e) {
            console.warn("[settings-encode]", e);
            return "";
        }
    }

    function buildRuntimeHeaders(extraHeaders, settings) {
        const headers = { ...(extraHeaders || {}) };
        const encoded = encodeRuntimeSettings(settings || loadSettings());
        if (encoded) headers["X-Autodev-Runtime"] = encoded;
        return headers;
    }

    function hasConfiguredRuntime(settings) {
        const current = normalizeSettings(settings || loadSettings());
        const hasRemoteKey = Boolean(
            current.secrets?.geminiApiKey ||
            current.secrets?.groqApiKey ||
            current.secrets?.groqApiKey2
        );
        const hasCustomEndpoint = Boolean(current.local_models?.localEndpointUrl);
        const hasSavedEndpoint = Array.isArray(current.customEndpoints) && current.customEndpoints.some((endpoint) => endpoint?.endpointUrl);
        return hasRemoteKey || hasCustomEndpoint || hasSavedEndpoint;
    }

    function safeRead(key, fallback) {
        try {
            const raw = localStorage.getItem(key);
            return raw ? JSON.parse(raw) : fallback;
        } catch (e) {
            console.warn("[settings-store]", e);
            return fallback;
        }
    }

    function safeWrite(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) {
            console.warn("[settings-store]", e);
        }
    }

    function normalizeSettings(settings) {
        let normalized = deepMerge(createDefaults(), runtimeToUiSettings(settings || {}));
        const preset = getPreset(normalized.workflowDefaults.workflowDepth);
        normalized.workflowDefaults.mode = normalized.workflowDefaults.mode || preset.mode;
        normalized.workflowDefaults.maxRetries = clampRetries(
            normalized.workflowDefaults.maxRetries || preset.maxRetries
        );
        normalized.workflowDefaults.defaultProvider =
            normalized.workflowDefaults.defaultProvider || "auto";
        normalized.providerRegistry.defaultProvider =
            normalized.providerRegistry.defaultProvider || normalized.workflowDefaults.defaultProvider;

        Object.keys(PROVIDER_CATALOG).forEach((provider) => {
            normalized.providerRegistry.providers[provider] =
                normalized.providerRegistry.providers[provider] || {
                    enabled: true,
                    selectedModel: provider === "custom" ? "" : provider,
                };
        });

        ROLE_ORDER.forEach((role) => {
            const current = normalized.providerRegistry.roleAssignments[role.id];
            if (!current) {
                const defaults = createDefaults().providerRegistry.roleAssignments[role.id];
                normalized.providerRegistry.roleAssignments[role.id] = defaults;
            }
        });

        if (!Array.isArray(normalized.customEndpoints)) normalized.customEndpoints = [];
        return normalized;
    }

    function loadSettings() {
        return normalizeSettings(safeRead(SETTINGS_KEY, createDefaults()));
    }

    function saveSettings(settings) {
        const normalized = normalizeSettings(settings);
        normalized.updatedAt = new Date().toISOString();
        safeWrite(SETTINGS_KEY, normalized);
        return normalized;
    }

    function updateSettings(updater) {
        const current = loadSettings();
        const draft = clone(current);
        const next = typeof updater === "function" ? updater(draft) || draft : deepMerge(draft, updater || {});
        return saveSettings(next);
    }

    function getPreset(id) {
        return WORKFLOW_PRESETS.find((preset) => preset.id === id) || WORKFLOW_PRESETS[1];
    }

    function clampRetries(value) {
        return Math.max(1, Math.min(6, Number(value) || 6));
    }

    function resolveMainPageProvider(provider) {
        if (provider === "groq_2") return "groq";
        if (!["auto", "gemini", "groq", "ollama", "custom"].includes(provider)) return "auto";
        return provider;
    }

    function getRunnerDefaults(settings) {
        const current = settings ? normalizeSettings(settings) : loadSettings();
        const preset = getPreset(current.workflowDefaults.workflowDepth);
        return {
            provider: resolveMainPageProvider(
                current.providerRegistry.defaultProvider || current.workflowDefaults.defaultProvider || "auto"
            ),
            refinePrompt: current.workflowDefaults.refinePrompt !== false,
            maxRetries: clampRetries(current.workflowDefaults.maxRetries || preset.maxRetries),
            mode: current.workflowDefaults.mode || preset.mode,
            workflowDepth: current.workflowDefaults.workflowDepth,
        };
    }

    function appendLog(entry) {
        const logs = safeRead(LOG_KEY, []);
        const record = {
            id: "log_" + Math.random().toString(36).slice(2, 10),
            timestamp: new Date().toISOString(),
            ...entry,
        };
        logs.unshift(record);
        safeWrite(LOG_KEY, logs.slice(0, 250));
        return record;
    }

    function getLogs() {
        return safeRead(LOG_KEY, []);
    }

    function clearLogs() {
        safeWrite(LOG_KEY, []);
    }

    function maskSecret(value) {
        if (!value) return "Not set";
        if (value.length <= 4) return "••••";
        return "••••••••" + value.slice(-4);
    }

    function buildProviderOptions(customEndpoints) {
        const options = Object.keys(PROVIDER_CATALOG).map((key) => ({
            value: key,
            label: PROVIDER_CATALOG[key].label,
        }));

        (customEndpoints || []).forEach((endpoint) => {
            options.push({
                value: "custom:" + endpoint.id,
                label: endpoint.name || endpoint.modelId || endpoint.endpointUrl,
            });
        });
        return options;
    }

    window.AutoDevSettings = {
        SETTINGS_KEY,
        LOG_KEY,
        PROVIDER_CATALOG,
        ROLE_ORDER,
        WORKFLOW_PRESETS,
        createDefaults,
        loadSettings,
        saveSettings,
        updateSettings,
        toRuntimeSettings,
        stripSecretsFromRuntime,
        mergeServerSettings,
        encodeRuntimeSettings,
        buildRuntimeHeaders,
        hasConfiguredRuntime,
        getPreset,
        getRunnerDefaults,
        appendLog,
        getLogs,
        clearLogs,
        maskSecret,
        buildProviderOptions,
    };
})(window);

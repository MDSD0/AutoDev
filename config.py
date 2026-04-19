"""
config.py — AutoDev v4 configuration.

v4 changes:
- Increased context budgets for all agents
- Added timeout configs per output category
- Bumped version to 4.0
"""
from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

_ROOT = os.path.dirname(os.path.abspath(__file__))


class Config:
    # ── Version ───────────────────────────────────────────────
    VERSION = "4.0"

    # ── Desktop mode ──────────────────────────────────────────
    IS_DESKTOP = os.getenv("AUTODEV_DESKTOP") == "1"

    # ── API Keys ──────────────────────────────────────────────
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
    GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")

    # ── Model Names ───────────────────────────────────────────
    GEMINI_MODEL    = os.getenv("GEMINI_MODEL",  "gemini-2.5-flash")
    GROQ_MODEL      = os.getenv("GROQ_MODEL",    "llama-3.3-70b-versatile")
    GROQ_MODEL_2    = os.getenv("GROQ_MODEL_2",  "llama-3.3-70b-versatile")
    GROQ_MODEL_3    = os.getenv("GROQ_MODEL_3",  "deepseek-r1-distill-llama-70b")
    OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",   "qwen2.5-coder:3b")
    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # ── Default provider: "auto" | "gemini" | "groq" | "groq_2" | "ollama" | "custom" ──
    DEFAULT_PROVIDER = os.getenv("DEFAULT_PROVIDER", "groq")

    # ── Directories ───────────────────────────────────────────
    _IS_VERCEL = os.getenv("VERCEL") == "1"
    _AUTODEV_HOME = os.path.join(os.path.expanduser("~"), ".autodev")

    if _IS_VERCEL:
        WORK_DIR     = "/tmp/autodev_coding"
        SESSIONS_DIR = "/tmp/autodev_sessions"
        VENV_DIR     = "/tmp/autodev_venv"
        QDRANT_PATH  = "/tmp/autodev_qdrant"
    elif IS_DESKTOP:
        WORK_DIR     = os.path.join(_AUTODEV_HOME, "coding")
        SESSIONS_DIR = os.path.join(_AUTODEV_HOME, "coding", "sessions")
        VENV_DIR     = os.path.join(_AUTODEV_HOME, "venv")
        QDRANT_PATH  = os.path.join(_AUTODEV_HOME, "qdrant_storage")
    else:
        WORK_DIR     = os.path.join(_ROOT, "coding")
        SESSIONS_DIR = os.path.join(_ROOT, "coding", "sessions")
        VENV_DIR     = os.path.join(_ROOT, ".autodev_venv")
        QDRANT_PATH  = os.path.join(_ROOT, "qdrant_storage")

    # ── Qdrant ────────────────────────────────────────────────
    QDRANT_SESSION_PREFIX = "autodev_session_"

    # ── Retry ─────────────────────────────────────────────────
    MAX_RETRIES = 6

    # ── v4: Role-scoped context budgets (approx characters) ──
    # Increased from v3 to prevent premature truncation of complex projects.
    CTX_SPEC_MAX_CHARS     = 6_000   # Spec Agent: raw task only
    CTX_PLANNER_MAX_CHARS  = 8_000   # Planner: spec + RAG context
    CTX_IMPL_MAX_CHARS     = 16_000  # Implementer: spec + plan + affected files + error
    CTX_REVIEWER_MAX_CHARS = 12_000  # Reviewer: spec + plan + exec + code summary

    # ── v4: Quality gates ─────────────────────────────────────
    MIN_REVIEW_SCORE = 7             # All dimensions must be >= this to PASS
    MAX_REPEATED_ERRORS = 2          # Escalate after N identical consecutive errors

    # ── v4: Convergence detection ─────────────────────────────
    CONVERGENCE_SAME_ERROR_THRESHOLD = 2  # Escalate after N retries with same error type

    # ── v4: Execution timeouts (per output category) ──────────
    EXECUTOR_TIMEOUT_CLI = 45        # CLI programs: timeout = failure
    EXECUTOR_TIMEOUT_SERVER = 10     # Server/Streamlit/GUI: timeout = success (stayed up)
    EXECUTOR_TIMEOUT_COMPILATION = 30  # C/C++/Java compilation step
    EXECUTOR_TIMEOUT_LLM_RESOLVE = 60  # LLM command resolution timeout

    # ── v4: Execution sandbox ─────────────────────────────────
    SANDBOX_TIMEOUT_SECS = 60
    SANDBOX_MEMORY_MB    = 512

    # ── Spec Schema (required keys) ──────────────────────────
    SPEC_REQUIRED_KEYS = [
        "problem_statement", "output_type", "expected_files", "entrypoint"
    ]
    PLAN_REQUIRED_KEYS = [
        "project_structure", "file_order", "packages", "entrypoint"
    ]

    @classmethod
    def available_providers(cls) -> list[str]:
        providers = []
        if cls.GOOGLE_API_KEY:
            providers.append("gemini")
        if cls.GROQ_API_KEY:
            providers.append("groq")
        if cls.GROQ_API_KEY_2:
            providers.append("groq_2")
        providers.append("ollama")  # always available if Ollama is running
        providers.append("custom")
        return providers

    @classmethod
    def all_models(cls) -> dict:
        """Return a structured dict of all model options for UI display."""
        return {
            "auto":   "Auto (Gemini → Groq 1 → Groq 2 → Ollama)",
            "gemini": f"Gemini ({cls.GEMINI_MODEL})",
            "groq":   f"Groq 1 ({cls.GROQ_MODEL})",
            "groq_2": f"Groq 2 ({cls.GROQ_MODEL_2})",
            "ollama": f"Ollama ({cls.OLLAMA_MODEL})",
            "custom": "Custom Local / OpenAI-Compatible Endpoint",
        }

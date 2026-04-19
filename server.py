"""
server.py — AutoDev v4 FastAPI backend.

v4 changes:
- Single RAG factory via get_rag() (eliminated duplicate _rag_instance)
- Updated for V4State and conversation mode
- pass output_category to rerun endpoint
- Updated version references
"""
from __future__ import annotations
import os
import json
import glob
import mimetypes
import shutil
import signal
import subprocess
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from .config import Config
from .graph import get_graph, stream_task, continue_task, refine_prompt_only
from .executor import launch_file, execute_project
from .session_memory import (
    list_sessions, load_state, load_files, load_history,
    delete_session, get_workspace_dir, rename_session, get_rag,
)
from .runtime_settings import (
    load_runtime_settings,
    redact_runtime_settings,
    save_runtime_settings,
    get_provider_secret,
    get_ollama_runtime,
    get_custom_runtime,
)
from .request_context import (
    COOKIE_NAME,
    decode_runtime_override,
    ensure_user_scope,
    reset_request_context,
    set_request_context,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_graph()  # warm up graph at startup
    yield


app = FastAPI(title=f"AutoDev v{Config.VERSION} API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_dir = os.path.join(os.path.dirname(__file__), "frontend")
os.makedirs(frontend_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")


def _env_truthy(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _allow_server_secrets_for_request(request: Request) -> bool:
    explicit = _env_truthy("AUTODEV_ALLOW_SERVER_SECRETS")
    if explicit is not None:
        return explicit
    client_host = (request.client.host if request.client else "").strip().lower()
    return client_host in {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def attach_request_context(request: Request, call_next):
    requested_scope = request.headers.get("X-Autodev-User") or request.cookies.get(COOKIE_NAME)
    user_scope = ensure_user_scope(requested_scope)
    runtime_override = decode_runtime_override(request.headers.get("X-Autodev-Runtime"))
    tokens = set_request_context(
        user_scope=user_scope,
        runtime_override=runtime_override,
        allow_server_secrets=_allow_server_secrets_for_request(request),
    )
    try:
        response = await call_next(request)
    finally:
        reset_request_context(tokens)

    response.set_cookie(
        COOKIE_NAME,
        user_scope,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 365,
        path="/",
    )
    return response


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(frontend_dir, "index.html"))


@app.get("/settings")
async def serve_settings():
    return FileResponse(os.path.join(frontend_dir, "settings.html"))


@app.get("/favicon.ico")
async def serve_favicon():
    favicon_path = os.path.join(frontend_dir, "favicon.svg")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return FileResponse(os.path.join(frontend_dir, "index.html"), status_code=204)


# ─────────────────────────────────────────────────────────────
# Request models
# ─────────────────────────────────────────────────────────────

class StreamRequest(BaseModel):
    task: str
    session_id: str = "default"
    model: str = "auto"
    refine_prompt: bool = True
    max_retries: int = 6
    mode: str = "auto"  # "auto" (smart routing) | "plan" (full pipeline) | "fast" (patch-only)


class ContinueRequest(BaseModel):
    session_id: str
    user_guidance: str
    model: str = "auto"
    max_retries: int = 3


class RefineRequest(BaseModel):
    task: str
    model: Optional[str] = "auto"


class RunFileRequest(BaseModel):
    filepath: str


class RerunRequest(BaseModel):
    session_id: str


class LaunchSessionRequest(BaseModel):
    session_id: str


class ProviderTestRequest(BaseModel):
    provider: str


class RuntimeSettingsRequest(BaseModel):
    settings: dict


class OllamaPullRequest(BaseModel):
    model_ref: str


class LlamaCppLaunchRequest(BaseModel):
    model_path: str
    huggingface_url: str = ""
    port: int = 8001
    context_size: int = 4096
    command: str = "llama-server"


class HuggingFaceDownloadRequest(BaseModel):
    repo_or_url: str
    include_pattern: str = "*.gguf"
    local_dir: str = ""
    revision: str = ""


class CustomEndpointTestRequest(BaseModel):
    endpoint_url: str
    model: str = "local-model"
    api_key: str = ""
    auth_header: str = "Authorization"


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@app.get("/status")
def status():
    return {"status": "running", "version": Config.VERSION}

@app.get("/ready")
def ready():
    return {"status": "ready", "version": Config.VERSION}


@app.get("/models")
def models():
    return Config.all_models()


def _normalize_openai_chat_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    if cleaned.endswith("/chat/completions"):
        return cleaned
    if cleaned.endswith("/v1"):
        return cleaned + "/chat/completions"
    return cleaned + "/v1/chat/completions"


def _parse_huggingface_repo_ref(value: str) -> tuple[str, str]:
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if "huggingface.co/" not in raw:
        return raw.strip("/"), ""

    tail = raw.split("huggingface.co/", 1)[1].split("?", 1)[0].strip("/")
    parts = [part for part in tail.split("/") if part]
    if len(parts) < 2:
        return "", ""
    repo_id = "/".join(parts[:2])

    file_path = ""
    if len(parts) > 4 and parts[2] in {"blob", "resolve", "tree"}:
        file_path = "/".join(parts[4:])
    return repo_id, file_path


def _safe_model_dir_name(repo_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in repo_id)[:120] or "model"


def _find_gguf_files(root: str) -> list[str]:
    matches = []
    for base, _, names in os.walk(root):
        for name in names:
            if name.lower().endswith(".gguf"):
                matches.append(os.path.join(base, name))
    matches.sort(key=lambda p: os.path.getsize(p) if os.path.exists(p) else 0, reverse=True)
    return matches


def _is_path_within_root(root: str, candidate: str) -> bool:
    try:
        root_real = os.path.realpath(root)
        candidate_real = os.path.realpath(candidate)
        return os.path.commonpath([root_real, candidate_real]) == root_real
    except (ValueError, OSError):
        return False


@app.get("/settings/bootstrap")
def settings_bootstrap():
    runtime = load_runtime_settings()
    ollama_model, ollama_base_url = get_ollama_runtime(runtime)
    providers = {
        "auto": {
            "label": "Auto",
            "configured": True,
            "default_model": "auto",
            "remote": True,
            "local": False,
            "supports": {
                "streaming": True,
                "json": True,
                "tool_use": False,
            },
        },
        "gemini": {
            "label": "Gemini",
            "configured": bool(get_provider_secret("gemini", runtime)),
            "default_model": Config.GEMINI_MODEL,
            "remote": True,
            "local": False,
            "supports": {
                "streaming": True,
                "json": True,
                "tool_use": True,
            },
        },
        "groq": {
            "label": "Groq",
            "configured": bool(get_provider_secret("groq", runtime)),
            "default_model": Config.GROQ_MODEL,
            "remote": True,
            "local": False,
            "supports": {
                "streaming": True,
                "json": True,
                "tool_use": False,
            },
        },
        "groq_2": {
            "label": "Groq Fallback",
            "configured": bool(get_provider_secret("groq_2", runtime)),
            "default_model": Config.GROQ_MODEL_2,
            "remote": True,
            "local": False,
            "supports": {
                "streaming": True,
                "json": True,
                "tool_use": False,
            },
        },
        "ollama": {
            "label": "Ollama",
            "configured": bool(ollama_base_url),
            "default_model": ollama_model,
            "remote": False,
            "local": True,
            "supports": {
                "streaming": True,
                "json": False,
                "tool_use": False,
            },
            "base_url": ollama_base_url,
        },
        "custom": {
            "label": "Custom Endpoint",
            "configured": bool(get_custom_runtime(runtime).get("endpoint_url")),
            "default_model": get_custom_runtime(runtime).get("model", ""),
            "remote": True,
            "local": False,
            "supports": {
                "streaming": True,
                "json": True,
                "tool_use": True,
            },
        },
    }

    available_providers = ["auto"]
    for key in ["gemini", "groq", "groq_2", "ollama", "custom"]:
        if providers[key]["configured"] or key == "ollama":
            available_providers.append(key)

    return {
        "version": Config.VERSION,
        "default_provider": runtime.get("provider_registry", {}).get("defaultProvider", Config.DEFAULT_PROVIDER),
        "max_retries": Config.MAX_RETRIES,
        "available_providers": available_providers,
        "models": Config.all_models(),
        "providers": providers,
        "runtime_settings": redact_runtime_settings(runtime),
        "pipeline_nodes": [
            "prompt_refiner",
            "spec_agent",
            "rag_retriever",
            "planner_agent",
            "dependency_installer",
            "implementer_agent",
            "executor",
            "error_classifier",
            "reviewer_agent",
        ],
    }


def _test_openai_compatible_endpoint(endpoint_url: str, model: str, api_key: str = "", auth_header: str = "Authorization"):
    import httpx

    url = endpoint_url.strip()
    if not url:
        return {"status": "error", "message": "Endpoint URL is required."}

    headers = {}
    if api_key:
        if auth_header.lower() == "authorization":
            headers[auth_header] = f"Bearer {api_key}"
        else:
            headers[auth_header] = api_key

    payload = {
        "model": model or "local-model",
        "messages": [
            {"role": "system", "content": "You are a connectivity test."},
            {"role": "user", "content": "Reply with READY"},
        ],
        "temperature": 0.0,
        "max_tokens": 8,
        "stream": False,
    }

    try:
        resp = httpx.post(_normalize_openai_chat_url(url), json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = ""
        if isinstance(data, dict):
            try:
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception:
                content = ""
        return {
            "provider": "custom",
            "status": "ready",
            "message": f"Endpoint responded successfully{f' ({content[:80]})' if content else ''}.",
        }
    except Exception as e:
        return {
            "provider": "custom",
            "status": "error",
            "message": str(e),
        }


@app.get("/settings/runtime")
def get_runtime_settings_endpoint():
    return redact_runtime_settings(load_runtime_settings())


@app.post("/settings/runtime")
def save_runtime_settings_endpoint(req: RuntimeSettingsRequest, request: Request):
    if not _allow_server_secrets_for_request(request):
        return {"status": "browser_only", "settings": redact_runtime_settings(req.settings)}
    saved = save_runtime_settings(req.settings)
    return {"status": "saved", "settings": saved}


@app.post("/settings/test_provider")
def settings_test_provider(req: ProviderTestRequest):
    provider = req.provider
    if provider == "custom":
        runtime = load_runtime_settings()
        custom = get_custom_runtime(runtime)
        if not custom.get("endpoint_url"):
            return {
                "provider": provider,
                "status": "error",
                "message": "No custom endpoint URL configured yet.",
            }
        return _test_openai_compatible_endpoint(custom["endpoint_url"], custom["model"], custom["api_key"], custom["auth_header"])

    if provider not in {"auto", "gemini", "groq", "groq_2", "ollama"}:
        raise HTTPException(400, f"Unsupported provider: {provider}")

    try:
        from .llm_utils import get_llm

        _, resolved = get_llm(provider)
        return {
            "provider": provider,
            "resolved_provider": resolved,
            "status": "ready",
            "message": f"{provider} is reachable and ready.",
        }
    except Exception as e:
        return {
            "provider": provider,
            "status": "error",
            "message": str(e),
        }


@app.post("/settings/test_custom_endpoint")
def test_custom_endpoint(req: CustomEndpointTestRequest):
    return _test_openai_compatible_endpoint(req.endpoint_url, req.model, req.api_key, req.auth_header)


@app.get("/settings/local_models/ollama_list")
def list_ollama_models():
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return {"status": "error", "message": "Ollama is not installed or not on PATH.", "models": []}
    except Exception as e:
        return {"status": "error", "message": str(e), "models": []}

    if proc.returncode != 0:
        return {"status": "error", "message": proc.stderr.strip() or proc.stdout.strip(), "models": []}

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    models = []
    for line in lines[1:]:
        parts = line.split()
        if parts:
            models.append({"name": parts[0], "raw": line})
    return {"status": "ready", "models": models}


@app.post("/settings/local_models/ollama_pull")
def pull_ollama_model(req: OllamaPullRequest):
    model_ref = req.model_ref.strip()
    if not model_ref:
        raise HTTPException(400, "model_ref is required")
    try:
        proc = subprocess.run(
            ["ollama", "pull", model_ref],
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
    except FileNotFoundError:
        return {"status": "error", "message": "Ollama is not installed or not on PATH."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

    if proc.returncode != 0:
        return {
            "status": "error",
            "message": proc.stderr.strip() or proc.stdout.strip() or "Ollama pull failed.",
        }

    runtime = load_runtime_settings()
    runtime["local_models"]["ollamaModel"] = model_ref
    runtime["provider_registry"]["defaultProvider"] = "ollama"
    runtime["workflow_defaults"]["defaultProvider"] = "ollama"
    save_runtime_settings(runtime)
    return {"status": "ready", "message": proc.stdout.strip() or f"Pulled {model_ref}", "model": model_ref}


@app.post("/settings/local_models/hf_download")
def download_huggingface_model(req: HuggingFaceDownloadRequest):
    repo_id, file_from_url = _parse_huggingface_repo_ref(req.repo_or_url)
    if not repo_id:
        raise HTTPException(400, "Enter a Hugging Face repo id or model file URL.")

    include_pattern = (req.include_pattern or "").strip()
    if file_from_url and (not include_pattern or include_pattern == "*.gguf"):
        include_pattern = file_from_url
    include_pattern = include_pattern or "*.gguf"
    local_root = os.path.expanduser(req.local_dir.strip()) if req.local_dir.strip() else os.path.join(Config.WORK_DIR, "local_models")
    target_dir = os.path.join(local_root, _safe_model_dir_name(repo_id))
    os.makedirs(target_dir, exist_ok=True)

    hf_bin = shutil.which("hf")
    if hf_bin is None:
        return {
            "status": "error",
            "message": "Hugging Face CLI `hf` was not found. Install it first, then retry the download.",
            "install_hint": "curl -LsSf https://hf.co/cli/install.sh | bash -s",
        }

    cmd = [hf_bin, "download", repo_id, "--include", include_pattern, "--local-dir", target_dir]
    if req.revision.strip():
        cmd.extend(["--revision", req.revision.strip()])

    env = os.environ.copy()
    runtime = load_runtime_settings()
    token = get_provider_secret("huggingface", runtime)
    if token:
        env["HF_TOKEN"] = token

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
            env=env,
        )
    except Exception as e:
        return {"status": "error", "message": str(e), "local_dir": target_dir}

    if proc.returncode != 0:
        return {
            "status": "error",
            "message": proc.stderr.strip() or proc.stdout.strip() or "Hugging Face download failed.",
            "local_dir": target_dir,
        }

    ggufs = _find_gguf_files(target_dir)
    selected = ggufs[0] if ggufs else ""
    runtime = load_runtime_settings()
    runtime["local_models"]["sourceType"] = "huggingface"
    runtime["local_models"]["huggingFaceUrl"] = req.repo_or_url.strip()
    runtime["local_models"]["hfDownloadPattern"] = include_pattern
    runtime["local_models"]["hfLocalDir"] = local_root
    if selected:
        runtime["local_models"]["modelFilePath"] = selected
    save_runtime_settings(runtime)

    return {
        "status": "ready" if selected else "downloaded",
        "message": f"Downloaded {repo_id}" + (f" and selected {os.path.basename(selected)}." if selected else ". No GGUF file was detected."),
        "repo_id": repo_id,
        "local_dir": target_dir,
        "model_path": selected,
        "files": ggufs[:20],
    }


@app.post("/settings/local_models/launch_llama_cpp")
def launch_llama_cpp(req: LlamaCppLaunchRequest):
    command = req.command.strip() or "llama-server"
    model_path = os.path.expanduser(req.model_path.strip())
    if not model_path:
        raise HTTPException(400, "model_path is required")
    if not os.path.isfile(model_path):
        raise HTTPException(400, f"Model file not found: {model_path}")
    if shutil.which(command) is None:
        return {"status": "error", "message": f"{command} was not found on PATH."}

    port = int(req.port or 8001)
    ctx = int(req.context_size or 4096)
    args = [
        command,
        "-m", model_path,
        "--port", str(port),
        "-c", str(ctx),
        "--host", "127.0.0.1",
    ]
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return {"status": "error", "message": str(e)}

    endpoint_url = f"http://127.0.0.1:{port}/v1/chat/completions"
    ready = False
    message = "Started llama.cpp server."
    for _ in range(20):
        time.sleep(0.75)
        result = _test_openai_compatible_endpoint(endpoint_url, os.path.basename(model_path), "", "Authorization")
        if result.get("status") == "ready":
            ready = True
            message = result.get("message", message)
            break

    runtime = load_runtime_settings()
    runtime["local_models"]["sourceType"] = "llama_cpp"
    runtime["local_models"]["huggingFaceUrl"] = req.huggingface_url.strip()
    runtime["local_models"]["modelFilePath"] = model_path
    runtime["local_models"]["llamaCppCommand"] = command
    runtime["local_models"]["llamaCppPort"] = port
    runtime["local_models"]["llamaCppContext"] = ctx
    runtime["local_models"]["llamaCppPid"] = proc.pid
    runtime["local_models"]["llamaCppStatus"] = "ready" if ready else "starting"
    runtime["local_models"]["localEndpointUrl"] = endpoint_url
    runtime["local_models"]["localEndpointModel"] = os.path.basename(model_path)
    runtime["local_models"]["selectedLocalProvider"] = "custom"
    runtime["provider_registry"]["defaultProvider"] = "custom"
    runtime["workflow_defaults"]["defaultProvider"] = "custom"
    save_runtime_settings(runtime)

    return {
        "status": "ready" if ready else "starting",
        "message": message,
        "pid": proc.pid,
        "endpoint_url": endpoint_url,
        "model": os.path.basename(model_path),
    }


@app.post("/settings/local_models/stop_llama_cpp")
def stop_llama_cpp():
    runtime = load_runtime_settings()
    pid = runtime.get("local_models", {}).get("llamaCppPid")
    if not pid:
        return {"status": "idle", "message": "No llama.cpp server PID saved."}
    try:
        os.kill(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    except Exception as e:
        return {"status": "error", "message": str(e)}

    runtime["local_models"]["llamaCppPid"] = None
    runtime["local_models"]["llamaCppStatus"] = "stopped"
    save_runtime_settings(runtime)
    return {"status": "stopped", "message": "Stopped llama.cpp server."}


@app.post("/refine_prompt")
def refine_endpoint(req: RefineRequest):
    try:
        refined = refine_prompt_only(req.task, provider=req.model or "auto")
        return {"original": req.task, "refined": refined}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/stream_task")
def stream_task_endpoint(req: StreamRequest):
    """SSE streaming endpoint — main task execution (auto-detects new vs. follow-up)."""
    def generator():
        try:
            for event in stream_task(
                task=req.task,
                session_id=req.session_id,
                provider=req.model,
                refine_prompt=req.refine_prompt,
                max_retries=req.max_retries,
                mode=req.mode,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'node': '__error__', 'data': {'error': str(e)}})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/continue_task")
def continue_task_endpoint(req: ContinueRequest):
    """Continue a failed task with user guidance (SSE streaming)."""
    def generator():
        try:
            for event in continue_task(
                session_id=req.session_id,
                user_guidance=req.user_guidance,
                provider=req.model,
                max_retries=req.max_retries,
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'node': '__error__', 'data': {'error': str(e)}})}\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/run_file")
def run_file(req: RunFileRequest):
    try:
        full_path = os.path.realpath(os.path.expanduser(req.filepath))
        if not _is_path_within_root(Config.SESSIONS_DIR, full_path):
            raise HTTPException(403, "Only files inside session workspaces can be launched")
        ok = launch_file(full_path)
        if ok:
            return {"status": "launched", "filepath": full_path}
        raise HTTPException(404, "File not found or could not launch")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/rerun_file")
def rerun_file_endpoint(req: RerunRequest):
    """Re-execute the project for a session."""
    state = load_state(req.session_id)
    if not state:
        raise HTTPException(404, "No saved state for this session")

    files = load_files(req.session_id)
    if not files:
        raise HTTPException(404, "No files found for this session")

    entrypoint = state.get("entrypoint", "main.py")
    output_type = state.get("output_type", "python")
    output_category = state.get("output_category", "cli_output")
    execution_command = state.get("execution_command", "")
    expected_output_files = state.get("expected_output_files", [])
    workspace = get_workspace_dir(req.session_id)

    report = execute_project(
        files, entrypoint, output_type, workspace,
        output_category=output_category,
        execution_command=execution_command,
        expected_output_files=expected_output_files,
    )
    return {
        "output": report.get("output", "(no output)"),
        "success": report.get("success", False),
        "report": report,
    }


# ─────────────────────────────────────────────────────────────
# Session endpoints
# ─────────────────────────────────────────────────────────────

@app.get("/sessions")
def get_sessions():
    return list_sessions()


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    """Return session history."""
    return load_history(session_id)


@app.get("/sessions/{session_id}/state")
def get_session_state(session_id: str):
    """Return full session state for resume."""
    state = load_state(session_id)
    if state is None:
        raise HTTPException(404, "Session not found")
    return state


@app.get("/session_files/{session_id}")
def session_files(session_id: str):
    workspace = get_workspace_dir(session_id)
    files = load_files(session_id)
    file_list = []
    for name, content in files.items():
        file_list.append({
            "name": name,
            "path": os.path.join(workspace, name),
            "content": content[:20000],
        })
    return {"files": file_list, "dir": workspace}


class RenameSessionRequest(BaseModel):
    title: str

@app.delete("/sessions/{session_id}")
def delete_session_endpoint(session_id: str):
    try:
        delete_session(session_id)
        return {"status": "deleted", "session_id": session_id}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/sessions/{session_id}/rename")
def rename_session_endpoint(session_id: str, req: RenameSessionRequest):
    try:
        rename_session(session_id, req.title)
        return {"status": "renamed", "session_id": session_id, "title": req.title}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────
# Dynamic Output Console endpoints
# ─────────────────────────────────────────────────────────────

_RENDERABLE_EXTS = {
    # Images
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".ico",
    # Documents / data
    ".html", ".htm", ".pdf", ".csv", ".json", ".xml", ".md", ".txt", ".log",
    # Video / audio
    ".mp4", ".webm", ".ogg", ".mp3", ".wav", ".avi",
    # Archives (show listing)
    ".eps",
}


@app.get("/output_files/{session_id}")
def list_output_files(session_id: str):
    """Scan session workspace for renderable output files (images, HTML, videos, etc)."""
    workspace = get_workspace_dir(session_id)
    if not os.path.isdir(workspace):
        return {"files": [], "workspace": workspace}

    results = []
    # Internal files that are NOT program output
    _INTERNAL_FILES = {
        "state.json", "history.json", "requirements.txt",
        "package.json", "package-lock.json", "node_modules",
        "Pipfile", "Pipfile.lock", "setup.py", "setup.cfg",
        "pyproject.toml", ".gitignore", "README.md",
    }
    # Source code extensions — not output
    _SOURCE_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss", ".less", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".sh", ".bash"}

    for root, dirs, filenames in os.walk(workspace):
        # Skip hidden directories and __pycache__
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for fname in sorted(filenames):
            # Skip wrapper files, hidden files, and internal files
            if fname.startswith(("_autodev_", ".")) or fname in _INTERNAL_FILES:
                continue
            ext = os.path.splitext(fname)[1].lower()
            # Skip source code files (only show output artifacts)
            if ext in _SOURCE_EXTS:
                continue
            if ext not in _RENDERABLE_EXTS:
                continue
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, workspace)
            mime, _ = mimetypes.guess_type(fname)
            stat = os.stat(full_path)
            # Categorize
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".ico"):
                category = "image"
            elif ext in (".html", ".htm"):
                category = "html"
            elif ext in (".mp4", ".webm", ".ogg", ".avi"):
                category = "video"
            elif ext in (".mp3", ".wav"):
                category = "audio"
            elif ext == ".pdf":
                category = "pdf"
            else:
                category = "text"

            results.append({
                "name": fname,
                "path": rel_path,
                "category": category,
                "mime": mime or "application/octet-stream",
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "url": f"/workspace_file/{session_id}/{rel_path}",
            })

    # Sort: images first, then HTML, then others; within each group by modified time desc
    category_order = {"image": 0, "html": 1, "video": 2, "audio": 3, "pdf": 4, "text": 5}
    results.sort(key=lambda f: (category_order.get(f["category"], 9), -f["modified"]))

    return {"files": results, "workspace": workspace}


@app.get("/workspace_file/{session_id}/{filepath:path}")
def serve_workspace_file(session_id: str, filepath: str):
    """Serve a file from a session's workspace directory."""
    workspace = get_workspace_dir(session_id)
    full_path = os.path.normpath(os.path.join(workspace, filepath))

    # Security: ensure path is within workspace
    if not _is_path_within_root(workspace, full_path):
        raise HTTPException(403, "Access denied")

    if not os.path.isfile(full_path):
        raise HTTPException(404, f"File not found: {filepath}")

    mime, _ = mimetypes.guess_type(full_path)
    return FileResponse(
        full_path,
        media_type=mime or "application/octet-stream",
        headers={
            "Cache-Control": "no-cache",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post("/wipe_rag")
def wipe_rag():
    """Wipe all RAG data using the shared RAG factory."""
    try:
        get_rag().wipe_all()
        return {"status": "wiped"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/launch_session")
def launch_session_endpoint(data: LaunchSessionRequest):
    sid = data.session_id
    workspace = get_workspace_dir(sid)
    state = load_state(sid)

    # Try to find the entrypoint
    entrypoint = ""
    if state:
        entrypoint = state.get("entrypoint") or state.get("spec", {}).get("entrypoint", "")
    
    if entrypoint:
        ep_path = os.path.join(workspace, entrypoint)
        if os.path.isfile(ep_path):
            launch_file(ep_path)
            return {"status": "launched", "file": ep_path}

    # Fallback: find any runnable file prioritizing .html then .py
    for ext in ["*.html", "*.py", "*.sh", "*.js", "*.c", "*.cpp", "*.java", "*.go", "*.rs"]:
        found_files = glob.glob(os.path.join(workspace, ext))
        found = [f for f in found_files if not os.path.basename(f).startswith(("_", "."))]
        if found:
            found.sort(key=lambda x: 0 if "main" in x.lower() or "index" in x.lower() else 1)
            launch_file(found[0])
            return {"status": "launched", "file": found[0]}

    raise HTTPException(404, "No executable found for session")


# ─────────────────────────────────────────────────────────────
# Desktop Setup Wizard endpoints
# ─────────────────────────────────────────────────────────────

class SetupKeysRequest(BaseModel):
    geminiApiKey: str = ""
    groqApiKey: str = ""
    groqApiKey2: str = ""
    huggingFaceApiKey: str = ""
    ollamaBaseUrl: str = ""
    ollamaModel: str = ""
    defaultProvider: str = "auto"


@app.get("/setup/status")
def setup_status():
    """Return whether the first-run setup has been completed."""
    runtime = load_runtime_settings()
    ollama_installed = False
    ollama_running = False
    ollama_models = []
    try:
        proc = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        ollama_installed = True
        if proc.returncode == 0:
            ollama_running = True
            lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
            for line in lines[1:]:
                parts = line.split()
                if parts:
                    ollama_models.append({"name": parts[0], "raw": line})
    except FileNotFoundError:
        pass
    except Exception:
        ollama_installed = True  # binary exists but errored

    return {
        "setupCompleted": runtime.get("setupCompleted", False),
        "setupCompletedAt": runtime.get("setupCompletedAt"),
        "desktopMode": runtime.get("desktopMode", False),
        "version": Config.VERSION,
        "ollamaInstalled": ollama_installed,
        "ollamaRunning": ollama_running,
        "ollamaModels": ollama_models,
        "hasApiKeys": bool(
            get_provider_secret("gemini", runtime)
            or get_provider_secret("groq", runtime)
        ),
        "defaultProvider": runtime.get("provider_registry", {}).get(
            "defaultProvider", Config.DEFAULT_PROVIDER
        ),
    }


@app.post("/setup/save_keys")
def setup_save_keys(req: SetupKeysRequest):
    """Bulk-save API keys during the setup wizard."""
    runtime = load_runtime_settings()
    if req.geminiApiKey:
        runtime["secrets"]["geminiApiKey"] = req.geminiApiKey
    if req.groqApiKey:
        runtime["secrets"]["groqApiKey"] = req.groqApiKey
    if req.groqApiKey2:
        runtime["secrets"]["groqApiKey2"] = req.groqApiKey2
    if req.huggingFaceApiKey:
        runtime["secrets"]["huggingFaceApiKey"] = req.huggingFaceApiKey
    if req.ollamaBaseUrl:
        runtime["secrets"]["ollamaBaseUrl"] = req.ollamaBaseUrl
        runtime["local_models"]["ollamaBaseUrl"] = req.ollamaBaseUrl
    if req.ollamaModel:
        runtime["local_models"]["ollamaModel"] = req.ollamaModel
    if req.defaultProvider:
        runtime["provider_registry"]["defaultProvider"] = req.defaultProvider
        runtime["workflow_defaults"]["defaultProvider"] = req.defaultProvider
    save_runtime_settings(runtime)
    return {"status": "saved"}


@app.post("/setup/complete")
def setup_complete():
    """Mark first-run setup as complete."""
    from datetime import datetime
    runtime = load_runtime_settings()
    runtime["setupCompleted"] = True
    runtime["setupCompletedAt"] = datetime.utcnow().isoformat() + "Z"
    runtime["desktopMode"] = True
    save_runtime_settings(runtime)
    return {"status": "completed", "setupCompletedAt": runtime["setupCompletedAt"]}


@app.post("/setup/test_connectivity")
def setup_test_connectivity():
    """Test all configured providers at once. Used by the setup wizard system check."""
    runtime = load_runtime_settings()
    results = {}
    for provider_name in ["gemini", "groq", "ollama"]:
        try:
            from .llm_utils import get_llm
            _, resolved = get_llm(provider_name)
            results[provider_name] = {
                "status": "ready",
                "resolved": resolved,
            }
        except Exception as e:
            results[provider_name] = {
                "status": "error",
                "message": str(e),
            }
    # Custom
    custom_rt = get_custom_runtime(runtime)
    if custom_rt.get("endpoint_url"):
        results["custom"] = _test_openai_compatible_endpoint(
            custom_rt["endpoint_url"], custom_rt["model"],
            custom_rt.get("api_key", ""), custom_rt.get("auth_header", "Authorization"),
        )
    return {"providers": results}

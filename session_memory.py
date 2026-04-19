"""
session_memory.py — AutoDev v4 per-session RAG + workspace state persistence.

v4 changes:
- Single RAG factory (eliminates duplicate singletons)
- Store conversation turns, code, and errors in RAG
- get_conversation_context() for follow-up messages
- store_code_snapshot() for semantic code retrieval
"""
from __future__ import annotations

import json
import os
import uuid
import shutil
from typing import Any

from .config import Config
from .request_context import get_user_scope


# ─────────────────────────────────────────────────────────────
# Singleton RAG Factory (v4: eliminates duplicate instances)
# ─────────────────────────────────────────────────────────────

_shared_rag: "SessionRAG | None" = None


def _sanitize_session_component(value: str) -> str:
    raw = (value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)[:120]
    if cleaned in {"", ".", ".."}:
        return "default"
    return cleaned


def _user_session_root() -> str:
    user_scope = get_user_scope()
    if not user_scope:
        return Config.SESSIONS_DIR
    return os.path.join(Config.SESSIONS_DIR, "users", _sanitize_session_component(user_scope))


def _rag_scope_prefix() -> str:
    user_scope = get_user_scope()
    if not user_scope:
        return ""
    return f"user_{_sanitize_session_component(user_scope)}__"


def get_rag() -> "SessionRAG":
    """Get the single shared RAG instance. Use this everywhere."""
    global _shared_rag
    if _shared_rag is None:
        _shared_rag = SessionRAG()
    return _shared_rag


# ─────────────────────────────────────────────────────────────
# RAG Memory (per-session Qdrant collections)
# ─────────────────────────────────────────────────────────────

class SessionRAG:
    """Per-session vector memory backed by Qdrant."""

    def __init__(self):
        self._model = None
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            os.makedirs(Config.QDRANT_PATH, exist_ok=True)
            self._client = QdrantClient(path=Config.QDRANT_PATH)

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")

    def _collection_name(self, session_id: str) -> str:
        scoped_session = f"{_rag_scope_prefix()}{_sanitize_session_component(session_id)}"
        return f"{Config.QDRANT_SESSION_PREFIX}{scoped_session}"

    def _ensure_collection(self, session_id: str):
        self._ensure_client()
        cname = self._collection_name(session_id)
        if not self._client.collection_exists(cname):
            from qdrant_client.models import Distance, VectorParams
            self._client.create_collection(
                collection_name=cname,
                vectors_config=VectorParams(size=384, distance=Distance.COSINE),
            )

    def add_memory(self, session_id: str, content: str, metadata: dict | None = None):
        """Store a memory entry in the session's collection."""
        if not content:
            return
        self._ensure_model()
        self._ensure_collection(session_id)
        vector = self._model.encode(content).tolist()
        from qdrant_client.models import PointStruct
        point_id = str(uuid.uuid4())
        payload = {"content": content}
        if metadata:
            payload.update(metadata)
        self._client.upsert(
            collection_name=self._collection_name(session_id),
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )

    def retrieve(self, session_id: str, query: str, limit: int = 5) -> str:
        """Retrieve relevant context from the session's collection."""
        try:
            self._ensure_model()
            self._ensure_collection(session_id)
            vector = self._model.encode(query).tolist()
            results = self._client.query_points(
                collection_name=self._collection_name(session_id),
                query=vector,
                limit=limit,
            )
            # Check if using the newer QueryResponse object wrapper
            if hasattr(results, "points"):
                hits = results.points
            else:
                hits = results
            texts = [hit.payload.get("content", "") for hit in hits if hit.payload]
            return "\n\n".join(texts) if texts else ""
        except Exception as e:
            print(f"  [SessionRAG] retrieve error: {e}")
            return ""

    def store_conversation_turn(self, session_id: str, role: str, name: str, content: str, turn: int = 0):
        """Store a conversation turn with metadata for retrieval."""
        self.add_memory(session_id, content, {
            "type": "conversation",
            "role": role,
            "agent_name": name,
            "turn": turn,
        })

    def store_code_snapshot(self, session_id: str, files: dict[str, str]):
        """Store code files with embeddings for semantic retrieval."""
        for filename, code in files.items():
            # Store a summary, not the full code (embeddings work better with summaries)
            summary = f"File: {filename}\nFirst 500 chars:\n{code[:500]}"
            self.add_memory(session_id, summary, {
                "type": "code",
                "filename": filename,
                "char_count": len(code),
            })

    def store_error_pattern(self, session_id: str, error_type: str, root_cause: str, fix_applied: str):
        """Store error patterns so the system can learn from failures."""
        content = f"Error [{error_type}]: {root_cause}\nFix applied: {fix_applied}"
        self.add_memory(session_id, content, {
            "type": "error_pattern",
            "error_type": error_type,
        })

    def get_conversation_context(self, session_id: str, query: str, limit: int = 5) -> str:
        """Retrieve semantically relevant conversation context for follow-ups."""
        return self.retrieve(session_id, query, limit=limit)

    def wipe_session(self, session_id: str):
        """Delete a session's RAG collection."""
        try:
            self._ensure_client()
            cname = self._collection_name(session_id)
            if self._client.collection_exists(cname):
                self._client.delete_collection(cname)
                print(f"  [SessionRAG] Wiped collection: {cname}")
        except Exception as e:
            print(f"  [SessionRAG] wipe error: {e}")

    def wipe_all(self):
        """Delete all session RAG collections."""
        try:
            self._ensure_client()
            collections = self._client.get_collections().collections
            scope_prefix = _rag_scope_prefix()
            user_prefix = f"{Config.QDRANT_SESSION_PREFIX}{scope_prefix}"
            for c in collections:
                if not c.name.startswith(Config.QDRANT_SESSION_PREFIX):
                    continue
                if scope_prefix and not c.name.startswith(user_prefix):
                    continue
                self._client.delete_collection(c.name)
                print(f"  [SessionRAG] Wiped: {c.name}")
        except Exception as e:
            print(f"  [SessionRAG] wipe_all error: {e}")


# ─────────────────────────────────────────────────────────────
# Workspace State Persistence
# ─────────────────────────────────────────────────────────────

def _session_dir(session_id: str) -> str:
    return os.path.join(_user_session_root(), _sanitize_session_component(session_id))


def save_state(session_id: str, state: dict):
    """Persist the full pipeline state to disk."""
    d = _session_dir(session_id)
    os.makedirs(d, exist_ok=True)

    # Serialise state — filter out non-serialisable objects
    serialisable = {}
    for k, v in state.items():
        try:
            json.dumps(v)
            serialisable[k] = v
        except (TypeError, ValueError):
            serialisable[k] = str(v)

    state_path = os.path.join(d, "state.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(serialisable, f, indent=2, default=str)


def load_state(session_id: str) -> dict | None:
    """Load persisted state. Returns None if not found."""
    state_path = os.path.join(_session_dir(session_id), "state.json")
    if not os.path.isfile(state_path):
        return None
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [Session] load error: {e}")
        return None


def save_files(session_id: str, files: dict[str, str]):
    """Write code files to the session workspace."""
    d = _session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    for filename, content in files.items():
        filepath = os.path.join(d, filename)
        fdir = os.path.dirname(filepath)
        if fdir and fdir != d:
            os.makedirs(fdir, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)


def load_files(session_id: str) -> dict[str, str]:
    """Read all code files from the session workspace."""
    d = _session_dir(session_id)
    if not os.path.isdir(d):
        return {}
    files = {}
    skip = {"state.json", "history.json", ".DS_Store", "screenshot.png", "a.out"}
    skip_exts = {
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
        ".db", ".sqlite", ".sqlite3", 
        ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".bin",
        ".pdf", ".zip", ".tar", ".gz", ".o", ".class", ".jar",
    }
    for root, dirs, filenames in os.walk(d):
        # Skip hidden dirs and __pycache__
        dirs[:] = [dd for dd in dirs if not dd.startswith(".") and dd != "__pycache__"]
        for fname in filenames:
            if fname in skip or fname.startswith("_"):
                continue
            if any(fname.lower().endswith(ext) for ext in skip_exts):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, d)
            try:
                # Check for null bytes to dynamically skip unknown binaries
                with open(fpath, "rb") as f:
                    chunk = f.read(1024)
                    if b"\x00" in chunk:
                        continue
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    files[rel] = f.read()
            except Exception:
                pass
    return files


def save_history(session_id: str, history: list[dict]):
    """Save chat history for session listing."""
    d = _session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "history.json"), "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, default=str)


def load_history(session_id: str) -> list[dict]:
    """Load chat history."""
    hp = os.path.join(_session_dir(session_id), "history.json")
    if os.path.isfile(hp):
        try:
            with open(hp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


# ─────────────────────────────────────────────────────────────
# Session Listing
# ─────────────────────────────────────────────────────────────

def _auto_title(task: str) -> str:
    """Generate a concise, intelligent title from a task description.
    
    Extracts the core intent in ≤ 40 chars, like:
      "Build a beautiful dark-themed scientific calculator..." → "Scientific Calculator"
      "Write a Python CLI tool that fetches the current Bitcoin..." → "Bitcoin Price CLI"
      "Create a responsive HTML portfolio page..." → "Portfolio Page"
      "hello" → "Chat"
      "sup" → "Chat"
    """
    if not task or not task.strip():
        return "Untitled"

    task = task.strip()

    # Chat/greeting detection
    chat_words = {"hi", "hello", "hey", "sup", "yo", "thanks", "thank you",
                  "ok", "okay", "cool", "nice", "great", "bye", "goodbye"}
    if task.lower().strip("!.,? ") in chat_words:
        return "Chat"

    # Strip common verb prefixes to get the noun phrase
    prefixes = [
        "build a ", "create a ", "make a ", "write a ", "generate a ",
        "develop a ", "implement a ", "design a ", "code a ",
        "build an ", "create an ", "make an ", "write an ",
        "build ", "create ", "make ", "write ", "generate ",
        "develop ", "implement ", "design ", "code ",
    ]
    core = task
    for p in prefixes:
        if core.lower().startswith(p):
            core = core[len(p):]
            break

    # Strip trailing detail after common delimiters
    for delim in [" with ", " that ", " which ", " using ", " in python",
                  " in html", " in css", " in js", " in javascript",
                  " in c++", " in c", " and ", " — ", " - "]:
        idx = core.lower().find(delim)
        if idx > 5:  # Keep at least 5 chars before cutting
            core = core[:idx]

    # Capitalize and truncate
    core = core.strip(" .,;:!?")
    if not core:
        return task[:35]

    # Title case it and limit length for cleaner display
    title = core[:30]
    words = title.split()
    if words:
        result = []
        for i, w in enumerate(words):
            if i == 0 or w.lower() not in ("a", "an", "the", "in", "on", "of", "for", "to", "by"):
                result.append(w.capitalize())
            else:
                result.append(w.lower())
        title = " ".join(result)

    return title


def list_sessions() -> list[dict]:
    """List all available sessions with metadata, sorted by most recent first."""
    sd = _user_session_root()
    scoped_user = get_user_scope()
    if not os.path.isdir(sd):
        return []

    sessions = []
    for d in os.listdir(sd):
        if not scoped_user and d == "users":
            continue
        dp = os.path.join(sd, d)
        if not os.path.isdir(dp):
            continue

        # Get modification time for sorting (most recent first)
        try:
            # Use the most recently modified file in the session dir
            mtime = max(
                os.path.getmtime(os.path.join(dp, f))
                for f in os.listdir(dp)
                if os.path.isfile(os.path.join(dp, f))
            )
        except (ValueError, OSError):
            mtime = os.path.getmtime(dp)

        info = {
            "id": d,
            "title": d,
            "updated_at": mtime,
            "status": "unknown",
            "output_type": "",
            "iterations": 0,
        }

        # From state.json — richest metadata
        state_path = os.path.join(dp, "state.json")
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r") as f:
                    state = json.load(f)

                # Title priority: custom_title > auto_title > raw task > session id
                custom = state.get("custom_title", "")
                auto = state.get("auto_title", "")
                raw_task = state.get("task", "")

                if custom:
                    info["title"] = custom[:50]
                elif auto:
                    info["title"] = auto[:50]
                elif raw_task:
                    info["title"] = _auto_title(raw_task)
                else:
                    info["title"] = d

                info["status"] = state.get("status", "unknown")
                info["iterations"] = state.get("iterations", 0)
                info["output_type"] = state.get("output_type", "")
            except Exception:
                pass

        # From history.json (fallback)
        elif os.path.isfile(os.path.join(dp, "history.json")):
            try:
                with open(os.path.join(dp, "history.json"), "r") as f:
                    history = json.load(f)
                first_user = next((m for m in history if m.get("name") == "User"), None)
                if first_user:
                    raw_task = first_user.get("content", d)
                    info["title"] = _auto_title(raw_task)
            except Exception:
                pass

        # Add preview of last user message if available
        if os.path.isfile(os.path.join(dp, "history.json")):
            try:
                with open(os.path.join(dp, "history.json"), "r", encoding="utf-8") as f:
                    hist = json.load(f)
                last_user_msg = next((m.get("content") for m in reversed(hist) if m.get("role") == "user"), None)
                if last_user_msg:
                    info["last_message"] = _auto_title(last_user_msg)
            except Exception:
                pass

        sessions.append(info)

    # Sort by most recently updated first
    sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)

    return sessions

def rename_session(session_id: str, new_title: str):
    """Rename a session by storing a custom_title in its state."""
    state = load_state(session_id)
    if state is not None:
        state["custom_title"] = new_title
        save_state(session_id, state)
    else:
        # Create a stub state.json if it doesn't exist
        save_state(session_id, {"custom_title": new_title})


def delete_session(session_id: str, rag: SessionRAG | None = None):
    """Delete a session's workspace and RAG collection."""
    d = _session_dir(session_id)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        print(f"  [Session] Deleted workspace: {d}")
    if rag:
        rag.wipe_session(session_id)
    else:
        get_rag().wipe_session(session_id)


def get_workspace_dir(session_id: str) -> str:
    """Return the workspace directory for a session, creating it if needed."""
    d = _session_dir(session_id)
    os.makedirs(d, exist_ok=True)
    return d

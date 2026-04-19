"""
execution_strategies.py — AutoDev v4 deterministic execution strategies.

Maps file types to compile/run commands. Used as fallback when LLM command
resolution fails, and as a reference for the LLM to suggest better commands.
"""
from __future__ import annotations

import os
import shutil


def _which(cmd: str) -> str | None:
    """Check if a command exists on PATH."""
    return shutil.which(cmd)


# ─────────────────────────────────────────────────────────────
# Language → Execution Strategy
# ─────────────────────────────────────────────────────────────

def get_execution_strategy(
    entrypoint: str,
    output_type: str,
    runtime: str,
    workspace_dir: str,
    python_path: str = "python3",
    files: dict[str, str] | None = None,
) -> dict:
    """
    Return a deterministic execution strategy.
    
    Returns {
        "compile_cmd": str | None,    # Command to compile (None if interpreted)
        "run_cmd": str,               # Command to run
        "timeout": int,               # Timeout in seconds
        "timeout_is_success": bool,   # If True, timeout means the program is running (server/GUI)
        "needs_compilation": bool,
        "description": str,
    }
    """
    ext = os.path.splitext(entrypoint)[1].lower()
    ep_path = os.path.join(workspace_dir, entrypoint)
    
    # ── Python ────────────────────────────────────────────
    if ext == ".py" or output_type == "python":
        # Check if it's a Streamlit app
        if output_type == "streamlit" or _is_streamlit(ep_path, files):
            return {
                "compile_cmd": None,
                "run_cmd": f"'{python_path}' -m streamlit run '{ep_path}' --server.headless=true --server.port=8501",
                "timeout": 10,
                "timeout_is_success": True,
                "needs_compilation": False,
                "description": "Streamlit app (headless)",
            }
        # Check if it's a GUI app (tkinter, PyQt, etc.)
        if _is_gui_app(ep_path, files):
            return {
                "compile_cmd": None,
                "run_cmd": f"'{python_path}' '{ep_path}'",
                "timeout": 8,
                "timeout_is_success": True,
                "needs_compilation": False,
                "description": "Python GUI app",
            }
        # Check if it's a Flask/FastAPI server
        if _is_server_app(ep_path, files):
            return {
                "compile_cmd": None,
                "run_cmd": f"'{python_path}' '{ep_path}'",
                "timeout": 8,
                "timeout_is_success": True,
                "needs_compilation": False,
                "description": "Python server app",
            }
        # Standard Python script
        return {
            "compile_cmd": None,
            "run_cmd": f"'{python_path}' '{ep_path}'",
            "timeout": 45,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "Python script",
        }

    # ── HTML ──────────────────────────────────────────────
    if ext in (".html", ".htm") or output_type == "html":
        return {
            "compile_cmd": None,
            "run_cmd": None,  # Special: HTML validation, not execution
            "timeout": 5,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "HTML validation + screenshot",
        }

    # ── JavaScript / TypeScript ───────────────────────────
    if ext in (".js", ".mjs") or output_type == "js":
        return {
            "compile_cmd": None,
            "run_cmd": f"node '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "Node.js script",
        }
    if ext == ".ts" or output_type == "typescript":
        ts_runner = "npx tsx" if _which("npx") else "npx ts-node"
        return {
            "compile_cmd": None,
            "run_cmd": f"{ts_runner} '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "TypeScript script",
        }

    # ── Shell ─────────────────────────────────────────────
    if ext in (".sh", ".bash", ".zsh") or output_type == "shell":
        return {
            "compile_cmd": None,
            "run_cmd": f"/bin/bash '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "Shell script",
        }

    # ── C ─────────────────────────────────────────────────
    if ext == ".c" or output_type == "c":
        binary = os.path.join(workspace_dir, "a.out")
        return {
            "compile_cmd": f"gcc '{ep_path}' -o '{binary}' -lm",
            "run_cmd": f"'{binary}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": True,
            "description": "C program (gcc)",
        }

    # ── C++ ───────────────────────────────────────────────
    if ext in (".cpp", ".cc", ".cxx") or output_type == "cpp":
        binary = os.path.join(workspace_dir, "a.out")
        return {
            "compile_cmd": f"g++ '{ep_path}' -o '{binary}' -std=c++17",
            "run_cmd": f"'{binary}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": True,
            "description": "C++ program (g++)",
        }

    # ── Java ──────────────────────────────────────────────
    if ext == ".java" or output_type == "java":
        class_name = os.path.splitext(os.path.basename(entrypoint))[0]
        return {
            "compile_cmd": f"javac '{ep_path}'",
            "run_cmd": f"java -cp '{workspace_dir}' {class_name}",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": True,
            "description": "Java program (javac + java)",
        }

    # ── Go ────────────────────────────────────────────────
    if ext == ".go" or output_type == "go":
        return {
            "compile_cmd": None,
            "run_cmd": f"go run '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,  # go run handles compilation
            "description": "Go program (go run)",
        }

    # ── Rust ──────────────────────────────────────────────
    if ext == ".rs" or output_type == "rust":
        binary = os.path.join(workspace_dir, "main")
        return {
            "compile_cmd": f"rustc '{ep_path}' -o '{binary}'",
            "run_cmd": f"'{binary}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": True,
            "description": "Rust program (rustc)",
        }

    # ── Ruby ──────────────────────────────────────────────
    if ext == ".rb" or output_type == "ruby":
        return {
            "compile_cmd": None,
            "run_cmd": f"ruby '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "Ruby script",
        }

    # ── PHP ───────────────────────────────────────────────
    if ext == ".php" or output_type == "php":
        return {
            "compile_cmd": None,
            "run_cmd": f"php '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "PHP script",
        }

    # ── R ─────────────────────────────────────────────────
    if ext in (".r", ".R") or output_type == "r":
        return {
            "compile_cmd": None,
            "run_cmd": f"Rscript '{ep_path}'",
            "timeout": 45,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "R script",
        }

    # ── Kotlin ────────────────────────────────────────────
    if ext in (".kt", ".kts") or output_type == "kotlin":
        if ext == ".kts":
            return {
                "compile_cmd": None,
                "run_cmd": f"kotlinc -script '{ep_path}'",
                "timeout": 45,
                "timeout_is_success": False,
                "needs_compilation": False,
                "description": "Kotlin script",
            }
        jar_path = os.path.join(workspace_dir, "main.jar")
        return {
            "compile_cmd": f"kotlinc '{ep_path}' -include-runtime -d '{jar_path}'",
            "run_cmd": f"java -jar '{jar_path}'",
            "timeout": 45,
            "timeout_is_success": False,
            "needs_compilation": True,
            "description": "Kotlin program",
        }

    # ── Swift ─────────────────────────────────────────────
    if ext == ".swift" or output_type == "swift":
        return {
            "compile_cmd": None,
            "run_cmd": f"swift '{ep_path}'",
            "timeout": 30,
            "timeout_is_success": False,
            "needs_compilation": False,
            "description": "Swift script",
        }

    # ── Fallback ──────────────────────────────────────────
    return {
        "compile_cmd": None,
        "run_cmd": f"'{python_path}' '{ep_path}'",
        "timeout": 30,
        "timeout_is_success": False,
        "needs_compilation": False,
        "description": f"Fallback (Python) for {ext}",
    }


# ─────────────────────────────────────────────────────────────
# File Content Sniffers
# ─────────────────────────────────────────────────────────────

def _read_file_content(filepath: str, files: dict | None) -> str:
    """Read file content from files dict or disk."""
    if files:
        basename = os.path.basename(filepath)
        for name, content in files.items():
            if name == basename or filepath.endswith(name):
                return content[:2000]
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read(2000)
    except Exception:
        return ""


def _is_streamlit(filepath: str, files: dict | None = None) -> bool:
    """Check if a Python file is a Streamlit app."""
    content = _read_file_content(filepath, files)
    return "import streamlit" in content or "from streamlit" in content


def _is_gui_app(filepath: str, files: dict | None = None) -> bool:
    """Check if a Python file is a GUI app (tkinter, PyQt, etc.).
    
    Note: pygame is NOT classified as GUI here — it is handled
    by the _detect_program_traits() wrapper system in executor.py
    which captures screenshots and classifies as file_output.
    """
    content = _read_file_content(filepath, files)
    gui_markers = [
        "import tkinter", "from tkinter",
        "import PyQt", "from PyQt",
        "import PySide", "from PySide",
        "import wx", "import kivy",
        ".mainloop()", "app.exec",
    ]
    return any(m in content for m in gui_markers)


def _is_server_app(filepath: str, files: dict | None = None) -> bool:
    """Check if a Python file is a web server."""
    content = _read_file_content(filepath, files)
    server_markers = [
        "from flask", "import flask",
        "from fastapi", "import fastapi",
        "from django", "import django",
        "from bottle", "import bottle",
        "HTTPServer", "socketserver",
        "app.run(", "uvicorn.run(",
    ]
    return any(m in content for m in server_markers)

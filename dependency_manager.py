"""
dependency_manager.py — Detect, install, and debug project dependencies.
Uses a shared virtual environment for isolation.
"""
from __future__ import annotations

import os
import re
import sys
import subprocess

from .config import Config


# ─────────────────────────────────────────────────────────────
# Venv Management
# ─────────────────────────────────────────────────────────────

def _ensure_venv() -> str:
    """Create the shared venv if it doesn't exist. Returns path to venv python."""
    venv_dir = Config.VENV_DIR
    if sys.platform == "win32":
        python_path = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_path = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        python_path = os.path.join(venv_dir, "bin", "python")
        pip_path = os.path.join(venv_dir, "bin", "pip")

    if os.path.isfile(python_path):
        return python_path

    print(f"  [DepManager] Creating shared venv at {venv_dir}...")
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", venv_dir],
            capture_output=True, text=True, timeout=60,
        )
        # Upgrade pip silently
        subprocess.run(
            [python_path, "-m", "pip", "install", "--upgrade", "pip"],
            capture_output=True, text=True, timeout=60,
        )
        print(f"  [DepManager] Venv created successfully.")
    except Exception as e:
        print(f"  [DepManager] Venv creation failed: {e}")

    return python_path


def get_venv_python() -> str:
    """Return path to the shared venv's Python interpreter."""
    return _ensure_venv()


# ─────────────────────────────────────────────────────────────
# Import Detection
# ─────────────────────────────────────────────────────────────

# Standard library modules (common ones) — don't need pip install
_STDLIB = {
    "os", "sys", "re", "json", "math", "random", "time", "datetime",
    "pathlib", "collections", "itertools", "functools", "typing",
    "subprocess", "shutil", "glob", "hashlib", "base64", "io",
    "copy", "enum", "dataclasses", "abc", "contextlib", "logging",
    "unittest", "argparse", "csv", "sqlite3", "http", "urllib",
    "socket", "threading", "multiprocessing", "queue", "signal",
    "struct", "array", "bisect", "heapq", "statistics", "decimal",
    "fractions", "string", "textwrap", "difflib", "pprint",
    "tempfile", "platform", "traceback", "warnings", "weakref",
    "html", "xml", "email", "uuid", "secrets", "hmac",
    "pickle", "shelve", "dbm", "gzip", "zipfile", "tarfile",
    "configparser", "tomllib", "ast", "dis", "inspect", "token",
    "tokenize", "codecs", "locale", "gettext", "operator",
    "venv", "ensurepip", "pip", "site", "sysconfig",
    "tkinter",  # included with most Python installs
}

# Common import-name → pip-name mappings
_IMPORT_TO_PIP = {
    "cv2": "opencv-python",
    "PIL": "pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "gi": "PyGObject",
    "wx": "wxPython",
    "serial": "pyserial",
    "usb": "pyusb",
    "dotenv": "python-dotenv",
    "jwt": "PyJWT",
    "crypto": "pycryptodome",
    "attr": "attrs",
    "dateutil": "python-dateutil",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "openpyxl": "openpyxl",
    "xlrd": "xlrd",
}


def detect_imports(code: str) -> set[str]:
    """Detect third-party imports from Python code."""
    imports = set()
    for line in code.split("\n"):
        line = line.strip()
        # import X or import X as Y
        m = re.match(r"^import\s+([\w.]+)", line)
        if m:
            top = m.group(1).split(".")[0]
            if top not in _STDLIB:
                imports.add(top)
        # from X import ...
        m = re.match(r"^from\s+([\w.]+)\s+import", line)
        if m:
            top = m.group(1).split(".")[0]
            if top not in _STDLIB:
                imports.add(top)
    return imports


def imports_to_packages(imports: set[str]) -> list[str]:
    """Convert import names to pip package names."""
    packages = []
    for imp in imports:
        pip_name = _IMPORT_TO_PIP.get(imp, imp)
        packages.append(pip_name)
    return sorted(set(packages))


# ─────────────────────────────────────────────────────────────
# Install Dependencies
# ─────────────────────────────────────────────────────────────

def install_dependencies(
    packages: list[str],
    workspace_dir: str,
    files: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """
    Install packages into the shared venv.

    Also scans code files for additional imports not listed in packages.

    Returns (success, install_log).
    """
    if not packages and not files:
        return True, "No dependencies to install."

    # Detect additional imports from code
    all_packages = set(packages)
    if files:
        for name, code in files.items():
            if name.endswith(".py"):
                detected = detect_imports(code)
                pip_names = imports_to_packages(detected)
                all_packages.update(pip_names)

    # Remove packages that are likely already available or are project-local
    all_packages.discard("")
    
    # Filter out standard library packages (to prevent e.g. `pip install secrets` from breaking the install)
    all_packages -= _STDLIB
    
    # Remove anything that looks like a local module (matches a project file)
    if files:
        local_modules = {f.replace(".py", "") for f in files if f.endswith(".py")}
        all_packages -= local_modules

    if not all_packages:
        return True, "No external dependencies needed."

    python_path = _ensure_venv()
    all_packages_list = sorted(all_packages)

    print(f"  [DepManager] Installing: {all_packages_list}")

    # Write requirements.txt to workspace
    os.makedirs(workspace_dir, exist_ok=True)
    req_path = os.path.join(workspace_dir, "requirements.txt")
    with open(req_path, "w") as f:
        f.write("\n".join(all_packages_list) + "\n")

    # Install
    try:
        result = subprocess.run(
            [python_path, "-m", "pip", "install", "-r", req_path,
             "--quiet", "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=120,
            cwd=workspace_dir,
        )
        log = ""
        if result.stdout.strip():
            log += result.stdout.strip()
        if result.stderr.strip():
            log += ("\n" if log else "") + result.stderr.strip()

        if result.returncode == 0:
            print(f"  [DepManager] Install successful.")
            return True, log or f"Installed: {', '.join(all_packages_list)}"
        else:
            print(f"  [DepManager] Install failed (exit {result.returncode})")
            return False, f"Install failed:\n{log}"

    except subprocess.TimeoutExpired:
        return False, "Dependency installation timed out (120s)."
    except Exception as e:
        return False, f"Install error: {e}"


def diagnose_install_failure(install_log: str, packages: list[str]) -> dict:
    """
    Analyse an install failure log and suggest fixes.

    Returns {
        "diagnosis": str,
        "suggested_fixes": [str],
        "packages_to_retry": [str],
        "packages_to_remove": [str],
    }
    """
    log_lower = install_log.lower()
    diagnosis = "Unknown install failure"
    fixes = []
    retry = list(packages)
    remove = []

    # Check for common patterns
    if "no matching distribution" in log_lower:
        # Extract the bad package name
        m = re.search(r"no matching distribution found for (\S+)", log_lower)
        bad_pkg = m.group(1) if m else "?"
        diagnosis = f"Package '{bad_pkg}' not found on PyPI"
        fixes.append(f"Check if '{bad_pkg}' is the correct pip package name")
        fixes.append("It might need a different name (e.g., 'Pillow' not 'PIL')")
        remove.append(bad_pkg)

    elif "could not find a version that satisfies" in log_lower:
        m = re.search(r"could not find a version that satisfies the requirement (\S+)", log_lower)
        bad_pkg = m.group(1) if m else "?"
        diagnosis = f"Version constraint for '{bad_pkg}' cannot be satisfied"
        fixes.append(f"Try removing version constraint from '{bad_pkg}'")
        # Retry without version constraint
        retry = [re.sub(r"[><=!]+.*", "", p) for p in packages]

    elif "permission denied" in log_lower or "permission error" in log_lower:
        diagnosis = "Permission denied during install"
        fixes.append("The virtual environment may be read-only")

    elif "error: subprocess-exited-with-error" in log_lower:
        diagnosis = "A package failed to build from source"
        fixes.append("May need system-level build tools (gcc, make, etc)")
        fixes.append("Try installing a pre-built wheel version")

    elif "connection" in log_lower or "timeout" in log_lower:
        diagnosis = "Network error during install"
        fixes.append("Check internet connection")
        fixes.append("Try again")

    return {
        "diagnosis": diagnosis,
        "suggested_fixes": fixes,
        "packages_to_retry": [p for p in retry if p not in remove],
        "packages_to_remove": remove,
    }

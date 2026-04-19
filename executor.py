"""
executor.py — AutoDev v4 Intelligent Execution Engine.

v4 complete rewrite:
- LLM-powered command resolution (always-on, not optional)
- Output-category-aware validation (CLI, file, GUI, server, browser)
- Post-execution file output detection (fixes image-generating programs)
- Compiled language support (compile + run)
- Smart timeout strategy per output category
- Deterministic fallback via execution_strategies.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import platform
import time
import webbrowser
import json
import re
import glob

from .config import Config
from .dependency_manager import get_venv_python
from .execution_strategies import get_execution_strategy
from .contracts import OutputCategory
from langchain_core.messages import HumanMessage, SystemMessage
from .llm_utils import invoke_llm


# ─────────────────────────────────────────────────────────────
# Save Files to Workspace
# ─────────────────────────────────────────────────────────────

def save_files_to_workspace(files: dict[str, str], workspace_dir: str) -> list[str]:
    """Write all project files to the workspace directory.
    Returns list of absolute paths for saved files."""
    os.makedirs(workspace_dir, exist_ok=True)
    saved = []
    for filename, content in files.items():
        filepath = os.path.join(workspace_dir, filename)
        # Create subdirectories if filename contains /
        os.makedirs(os.path.dirname(filepath), exist_ok=True) if os.path.dirname(filepath) != workspace_dir else None
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        # Make shell scripts executable
        if filename.endswith((".sh", ".bash", ".zsh")):
            os.chmod(filepath, 0o755)
        saved.append(os.path.abspath(filepath))
    return saved


# ─────────────────────────────────────────────────────────────
# Pre-execution File Snapshot (for detecting new output files)
# ─────────────────────────────────────────────────────────────

def _snapshot_workspace_files(workspace_dir: str) -> set[str]:
    """Take a snapshot of all files in workspace before execution."""
    snapshot = set()
    for root, dirs, filenames in os.walk(workspace_dir):
        for f in filenames:
            snapshot.add(os.path.join(root, f))
    return snapshot


def _detect_new_files(workspace_dir: str, before: set[str]) -> list[str]:
    """Detect files created during execution."""
    after = _snapshot_workspace_files(workspace_dir)
    new_files = after - before
    # Filter to interesting output files
    output_exts = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff",
        ".pdf", ".csv", ".xlsx", ".json", ".xml", ".txt", ".log",
        ".mp3", ".wav", ".mp4", ".avi", ".html", ".md",
    }
    result = []
    for f in sorted(new_files):
        ext = os.path.splitext(f)[1].lower()
        if ext in output_exts:
            result.append(f)
    return result


# ─────────────────────────────────────────────────────────────
# Program Trait Detection (smart execution strategy)
# ─────────────────────────────────────────────────────────────

_MATPLOTLIB_MARKERS = (
    "import matplotlib", "from matplotlib",
    "import seaborn", "from seaborn",
)
_PLT_SHOW_MARKERS = ("plt.show()", "pyplot.show()", "figure.show()")
_PLT_SAVE_MARKERS = ("plt.savefig(", "pyplot.savefig(", "fig.savefig(", "figure.savefig(", ".savefig(")
_PYGAME_MARKERS = ("import pygame", "from pygame")
_TURTLE_MARKERS = ("import turtle", "from turtle")
_PILLOW_MARKERS = (
    "from PIL", "import PIL",
    "from pillow", "import pillow",
)
_PIL_SAVE_MARKERS = (".save(", "img.save(", "image.save(", "im.save(")
_PIL_SHOW_MARKERS = (".show()", "img.show()", "image.show()", "im.show()")
_PLOTLY_MARKERS = ("import plotly", "from plotly")
_PLOTLY_SHOW_MARKERS = (".show()", "fig.show()", "figure.show()")
_PLOTLY_SAVE_MARKERS = ("write_image(", "write_html(", "to_image(", "to_html(")
_CV2_MARKERS = ("import cv2", "from cv2")
_CV2_SHOW_MARKERS = ("cv2.imshow(", "cv2.waitKey(")
_GUI_MARKERS = (
    "import tkinter", "from tkinter",
    "import PyQt", "from PyQt",
    "import PySide", "from PySide",
    "import wx", "import kivy",
    ".mainloop()", "app.exec",
)
_SERVER_MARKERS = (
    "from flask", "import flask",
    "from fastapi", "import fastapi",
    "from django", "import django",
    "HTTPServer", "socketserver",
    "app.run(", "uvicorn.run(",
    "import streamlit", "from streamlit",
)
_INPUT_MARKERS = ("input(",)
_AUDIO_MARKERS = (
    "import pygame.mixer", "pygame.mixer",
    "import simpleaudio", "import playsound",
    "import sounddevice", "import pyaudio",
)


def _detect_program_traits(files: dict[str, str]) -> dict:
    """Scan source files to detect runtime traits the spec may have missed.

    Catches cases where the LLM-generated spec has the wrong output_category,
    e.g. a matplotlib chart marked as cli_output instead of file_output.
    Returns a dict that the executor uses to choose the right strategy.
    
    v4.2: Expanded to detect PIL/Pillow, Plotly, OpenCV, turtle, pygame,
    interactive input(), and audio programs.  Each detected trait gets an
    appropriate wrapper so visual/graphical output is captured as files.
    """
    traits = {
        "has_matplotlib": False,
        "has_plt_show": False,
        "has_plt_savefig": False,
        "has_pygame": False,
        "has_turtle": False,
        "has_pillow": False,
        "has_pil_show": False,
        "has_pil_save": False,
        "has_plotly": False,
        "has_plotly_show": False,
        "has_plotly_save": False,
        "has_cv2": False,
        "has_cv2_show": False,
        "has_gui": False,
        "has_server": False,
        "has_input": False,
        "has_audio": False,
        "recommended_category": None,
        "needs_matplotlib_wrapper": False,
        "needs_pygame_wrapper": False,
        "needs_turtle_wrapper": False,
        "needs_pillow_wrapper": False,
        "needs_plotly_wrapper": False,
        "needs_cv2_wrapper": False,
        "needs_input_wrapper": False,
    }

    for name, content in files.items():
        if not name.endswith(".py"):
            continue
        # Matplotlib / Seaborn
        if any(m in content for m in _MATPLOTLIB_MARKERS):
            traits["has_matplotlib"] = True
        if any(m in content for m in _PLT_SHOW_MARKERS):
            traits["has_plt_show"] = True
        if any(m in content for m in _PLT_SAVE_MARKERS):
            traits["has_plt_savefig"] = True
        # Pygame
        if any(m in content for m in _PYGAME_MARKERS):
            traits["has_pygame"] = True
        # Turtle
        if any(m in content for m in _TURTLE_MARKERS):
            traits["has_turtle"] = True
        # PIL / Pillow
        if any(m in content for m in _PILLOW_MARKERS):
            traits["has_pillow"] = True
        if traits["has_pillow"]:
            if any(m in content for m in _PIL_SHOW_MARKERS):
                traits["has_pil_show"] = True
            if any(m in content for m in _PIL_SAVE_MARKERS):
                traits["has_pil_save"] = True
        # Plotly
        if any(m in content for m in _PLOTLY_MARKERS):
            traits["has_plotly"] = True
        if traits["has_plotly"]:
            if any(m in content for m in _PLOTLY_SHOW_MARKERS):
                traits["has_plotly_show"] = True
            if any(m in content for m in _PLOTLY_SAVE_MARKERS):
                traits["has_plotly_save"] = True
        # OpenCV
        if any(m in content for m in _CV2_MARKERS):
            traits["has_cv2"] = True
        if traits["has_cv2"] and any(m in content for m in _CV2_SHOW_MARKERS):
            traits["has_cv2_show"] = True
        # GUI frameworks
        if any(m in content for m in _GUI_MARKERS):
            traits["has_gui"] = True
        # Servers
        if any(m in content for m in _SERVER_MARKERS):
            traits["has_server"] = True
        # Interactive input()
        if any(m in content for m in _INPUT_MARKERS):
            # Ignore input() inside comments or strings (rough heuristic)
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "input(" in stripped:
                    traits["has_input"] = True
                    break
        # Audio
        if any(m in content for m in _AUDIO_MARKERS):
            traits["has_audio"] = True

    # ── Determine smart overrides ─────────────────────────
    # Priority order: server > pygame > turtle > gui > plotly > matplotlib > pillow > cv2

    if traits["has_server"]:
        traits["recommended_category"] = OutputCategory.SERVER_OUTPUT.value
    elif traits["has_pygame"]:
        # Pygame games: wrap to auto-capture a screenshot of the running game
        traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
        traits["needs_pygame_wrapper"] = True
    elif traits["has_turtle"]:
        # Turtle graphics: wrap to auto-save the canvas as an image
        traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
        traits["needs_turtle_wrapper"] = True
    elif traits["has_gui"]:
        traits["recommended_category"] = OutputCategory.GUI_OUTPUT.value
    elif traits["has_plotly"]:
        if not traits["has_plotly_save"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
            traits["needs_plotly_wrapper"] = True
        else:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
    elif traits["has_matplotlib"]:
        if traits["has_plt_show"] and not traits["has_plt_savefig"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
            traits["needs_matplotlib_wrapper"] = True
        elif not traits["has_plt_show"] and not traits["has_plt_savefig"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
            traits["needs_matplotlib_wrapper"] = True
        elif traits["has_plt_savefig"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
    elif traits["has_pillow"]:
        if traits["has_pil_show"] and not traits["has_pil_save"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
            traits["needs_pillow_wrapper"] = True
        elif not traits["has_pil_show"] and not traits["has_pil_save"]:
            # Pillow imported but neither show nor save — wrapper to be safe
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
            traits["needs_pillow_wrapper"] = True
        elif traits["has_pil_save"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
    elif traits["has_cv2"]:
        if traits["has_cv2_show"]:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value
            traits["needs_cv2_wrapper"] = True
        else:
            traits["recommended_category"] = OutputCategory.FILE_OUTPUT.value

    # Interactive input() detection — add wrapper to mock stdin so program doesn't hang
    if traits["has_input"] and not traits["has_gui"] and not traits["has_server"]:
        traits["needs_input_wrapper"] = True

    return traits


def _create_matplotlib_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that auto-saves matplotlib figures instead of opening windows.

    Patches plt.show() to save every open figure to output_N.png, then close them.
    Returns the wrapper filename (relative to workspace).
    """
    wrapper_lines = [
        '"""AutoDev matplotlib auto-save wrapper — do not edit."""',
        'import os, sys',
        '',
        '# Force non-interactive backend BEFORE any matplotlib import',
        'os.environ["MPLBACKEND"] = "Agg"',
        '',
        '# Fix sys.argv to point at the real script',
        'sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), ' + repr(entrypoint) + ')',
        '',
        '# ── Monkey-patch plt.show() ──────────────────────────',
        '_autodev_fig_counter = 0',
        '',
        'def _autodev_patch():',
        '    global _autodev_fig_counter',
        '    try:',
        '        import matplotlib',
        '        matplotlib.use("Agg")',
        '        import matplotlib.pyplot as plt',
        '',
        '        def _show(*a, **kw):',
        '            global _autodev_fig_counter',
        '            figs = [plt.figure(n) for n in plt.get_fignums()]',
        '            if not figs:',
        '                return',
        '            for fig in figs:',
        '                _autodev_fig_counter += 1',
        '                p = os.path.join(os.getcwd(), f"output_{_autodev_fig_counter}.png")',
        '                fig.savefig(p, dpi=150, bbox_inches="tight")',
        '                print(f"[AutoDev] Saved figure -> output_{_autodev_fig_counter}.png")',
        '            plt.close("all")',
        '',
        '        plt.show = _show',
        '    except ImportError:',
        '        pass',
        '',
        '_autodev_patch()',
        '',
        '# ── Execute the real script ──────────────────────────',
        '_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), ' + repr(entrypoint) + ')',
        'with open(_target, "r", encoding="utf-8") as _f:',
        '    _code = compile(_f.read(), _target, "exec")',
        'exec(_code, {"__name__": "__main__", "__file__": _target})',
    ]
    wrapper_content = "\n".join(wrapper_lines) + "\n"

    wrapper_name = "_autodev_mpl_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper_content)
    return wrapper_name


def _create_pygame_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that runs a pygame program headlessly and captures screenshots.

    Strategy:
    - Set SDL_VIDEODRIVER=dummy so pygame can initialise without a real display.
    - Monkey-patch pygame.display.flip/update to auto-capture the surface.
    - After a few frames (or on quit), save screenshots to output_N.png.
    - Let the game loop run for a limited time, then force-quit gracefully.
    """
    wrapper = f'''"""AutoDev pygame auto-screenshot wrapper — do not edit."""
import os, sys, time

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

_autodev_frame = 0
_autodev_captures = []
_autodev_start = time.time()
_autodev_max_runtime = 6  # seconds
_autodev_capture_frames = {{1, 5, 15, 30, 60, 120}}  # capture at these frame numbers

try:
    import pygame
    _orig_flip = pygame.display.flip
    _orig_update = pygame.display.update

    def _capture_screen():
        global _autodev_frame
        _autodev_frame += 1
        if _autodev_frame in _autodev_capture_frames:
            try:
                surface = pygame.display.get_surface()
                if surface:
                    fname = f"output_frame_{{_autodev_frame}}.png"
                    fpath = os.path.join(os.getcwd(), fname)
                    pygame.image.save(surface, fpath)
                    _autodev_captures.append(fname)
                    print(f"[AutoDev] Captured pygame frame {{_autodev_frame}} -> {{fname}}")
            except Exception:
                pass
        # Force quit after max runtime
        if time.time() - _autodev_start > _autodev_max_runtime:
            # Take one final screenshot before quitting
            try:
                surface = pygame.display.get_surface()
                if surface:
                    fpath = os.path.join(os.getcwd(), "output_final.png")
                    pygame.image.save(surface, fpath)
                    _autodev_captures.append("output_final.png")
                    print("[AutoDev] Captured final pygame screenshot -> output_final.png")
            except Exception:
                pass
            print(f"[AutoDev] Pygame: {{_autodev_frame}} frames rendered in {{_autodev_max_runtime}}s")
            pygame.quit()
            sys.exit(0)

    def _patched_flip():
        _orig_flip()
        _capture_screen()

    def _patched_update(*args, **kwargs):
        _orig_update(*args, **kwargs)
        _capture_screen()

    pygame.display.flip = _patched_flip
    pygame.display.update = _patched_update

except ImportError:
    pass

# ── Execute the real script ──────────────────────────
_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})
try:
    with open(_target, "r", encoding="utf-8") as _f:
        _code = compile(_f.read(), _target, "exec")
    exec(_code, {{"__name__": "__main__", "__file__": _target}})
except SystemExit:
    pass  # Normal exit from our force-quit
finally:
    if _autodev_captures:
        print(f"[AutoDev] Pygame wrapper captured {{len(_autodev_captures)}} screenshot(s)")
    else:
        print("[AutoDev] Pygame wrapper: no screenshots captured (game may not have rendered)")
'''
    wrapper_name = "_autodev_pygame_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    return wrapper_name


def _create_turtle_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that runs turtle graphics headlessly and saves the canvas.

    Uses Xvfb-style virtual display or Tkinter's Agg if available,
    falls back to running the program and capturing the canvas as EPS → PNG.
    """
    wrapper = f'''"""AutoDev turtle auto-save wrapper — do not edit."""
import os, sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

try:
    import turtle

    # Monkey-patch turtle.done/mainloop/exitonclick to save canvas and exit
    _autodev_saved = False

    def _autodev_save_canvas():
        global _autodev_saved
        if _autodev_saved:
            return
        _autodev_saved = True
        try:
            screen = turtle.getscreen()
            canvas = screen.getcanvas()
            # Save as EPS first
            eps_path = os.path.join(os.getcwd(), "output_turtle.eps")
            canvas.postscript(file=eps_path)
            print(f"[AutoDev] Saved turtle canvas -> output_turtle.eps")
            # Try to convert EPS to PNG using PIL if available
            try:
                from PIL import Image
                img = Image.open(eps_path)
                png_path = os.path.join(os.getcwd(), "output_turtle.png")
                img.save(png_path, "PNG")
                print(f"[AutoDev] Converted to PNG -> output_turtle.png")
            except Exception:
                print("[AutoDev] PIL not available for EPS->PNG conversion, EPS saved")
        except Exception as e:
            print(f"[AutoDev] Turtle canvas save failed: {{e}}")

    _orig_done = turtle.done
    _orig_mainloop = turtle.mainloop
    _orig_exitonclick = turtle.exitonclick

    def _patched_done():
        _autodev_save_canvas()
        try:
            turtle.bye()
        except Exception:
            pass

    def _patched_mainloop():
        _autodev_save_canvas()
        try:
            turtle.bye()
        except Exception:
            pass

    def _patched_exitonclick():
        _autodev_save_canvas()
        try:
            turtle.bye()
        except Exception:
            pass

    turtle.done = _patched_done
    turtle.mainloop = _patched_mainloop
    turtle.exitonclick = _patched_exitonclick

    # Also patch Screen.mainloop
    try:
        _orig_screen_mainloop = turtle.Screen.mainloop
        turtle.Screen.mainloop = lambda self: _patched_mainloop()
    except Exception:
        pass

except ImportError:
    pass

# Also patch input() to prevent blocking
import builtins
_orig_input = builtins.input
_input_counter = 0
def _mock_input(prompt=""):
    global _input_counter
    _input_counter += 1
    print(f"[AutoDev] Auto-responding to input() prompt: {{prompt}}")
    return ""
builtins.input = _mock_input

try:
    with open(_target, "r", encoding="utf-8") as _f:
        _code = compile(_f.read(), _target, "exec")
    exec(_code, {{"__name__": "__main__", "__file__": _target}})
except (SystemExit, turtle.Terminator):
    pass
except Exception as e:
    print(f"[AutoDev] Script error: {{e}}")
finally:
    if not _autodev_saved:
        try:
            _autodev_save_canvas()
        except Exception:
            pass
'''
    wrapper_name = "_autodev_turtle_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    return wrapper_name


def _create_pillow_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that intercepts PIL Image.show() and saves to file instead."""
    wrapper = f'''"""AutoDev PIL/Pillow auto-save wrapper — do not edit."""
import os, sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

_autodev_img_counter = 0

try:
    from PIL import Image as _PIL_Image

    _orig_show = _PIL_Image.Image.show

    def _patched_show(self, title=None, **kwargs):
        global _autodev_img_counter
        _autodev_img_counter += 1
        fname = f"output_{{_autodev_img_counter}}.png"
        fpath = os.path.join(os.getcwd(), fname)
        self.save(fpath)
        print(f"[AutoDev] Saved PIL image -> {{fname}}")

    _PIL_Image.Image.show = _patched_show
except ImportError:
    pass

_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})
with open(_target, "r", encoding="utf-8") as _f:
    _code = compile(_f.read(), _target, "exec")
exec(_code, {{"__name__": "__main__", "__file__": _target}})
'''
    wrapper_name = "_autodev_pil_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    return wrapper_name


def _create_plotly_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that intercepts plotly fig.show() and saves to HTML/PNG."""
    wrapper = f'''"""AutoDev plotly auto-save wrapper — do not edit."""
import os, sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

_autodev_plotly_counter = 0

try:
    import plotly.graph_objects as _go

    _orig_show = _go.Figure.show

    def _patched_show(self, *args, **kwargs):
        global _autodev_plotly_counter
        _autodev_plotly_counter += 1
        # Save as HTML (always works)
        html_path = os.path.join(os.getcwd(), f"output_{{_autodev_plotly_counter}}.html")
        self.write_html(html_path)
        print(f"[AutoDev] Saved plotly figure -> output_{{_autodev_plotly_counter}}.html")
        # Try to save as PNG if kaleido is installed
        try:
            png_path = os.path.join(os.getcwd(), f"output_{{_autodev_plotly_counter}}.png")
            self.write_image(png_path)
            print(f"[AutoDev] Saved plotly figure -> output_{{_autodev_plotly_counter}}.png")
        except Exception:
            pass

    _go.Figure.show = _patched_show
except ImportError:
    pass

_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})
with open(_target, "r", encoding="utf-8") as _f:
    _code = compile(_f.read(), _target, "exec")
exec(_code, {{"__name__": "__main__", "__file__": _target}})
'''
    wrapper_name = "_autodev_plotly_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    return wrapper_name


def _create_cv2_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that intercepts cv2.imshow/waitKey and saves to file."""
    wrapper = f'''"""AutoDev OpenCV auto-save wrapper — do not edit."""
import os, sys

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

_autodev_cv_counter = 0
_autodev_cv_windows = {{}}

try:
    import cv2 as _cv2

    _orig_imshow = _cv2.imshow
    _orig_waitkey = _cv2.waitKey

    def _patched_imshow(winname, mat):
        global _autodev_cv_counter
        _autodev_cv_windows[winname] = mat
        _autodev_cv_counter += 1
        fname = f"output_cv_{{_autodev_cv_counter}}.png"
        fpath = os.path.join(os.getcwd(), fname)
        _cv2.imwrite(fpath, mat)
        print(f"[AutoDev] Saved cv2 image -> {{fname}}")

    def _patched_waitkey(delay=0):
        # Return ESC key immediately so programs don't block
        return 27

    _cv2.imshow = _patched_imshow
    _cv2.waitKey = _patched_waitkey
except ImportError:
    pass

_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})
with open(_target, "r", encoding="utf-8") as _f:
    _code = compile(_f.read(), _target, "exec")
exec(_code, {{"__name__": "__main__", "__file__": _target}})
'''
    wrapper_name = "_autodev_cv2_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    return wrapper_name


def _create_input_wrapper(workspace_dir: str, entrypoint: str) -> str:
    """Create a wrapper that provides mock responses to input() calls to prevent hangs."""
    wrapper = f'''"""AutoDev input() auto-responder wrapper — do not edit."""
import os, sys, builtins

sys.argv[0] = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})

_input_counter = 0
_mock_responses = ["1", "hello", "yes", "42", "test", "3.14", "Alice", "0", "quit", "exit"]

_orig_input = builtins.input

def _mock_input(prompt=""):
    global _input_counter
    resp = _mock_responses[_input_counter % len(_mock_responses)]
    _input_counter += 1
    print(f"[AutoDev] input({{repr(prompt)}}) -> {{repr(resp)}} (auto-response #{{_input_counter}})")
    return resp

builtins.input = _mock_input

_target = os.path.join(os.path.dirname(os.path.abspath(__file__)), {repr(entrypoint)})
with open(_target, "r", encoding="utf-8") as _f:
    _code = compile(_f.read(), _target, "exec")
exec(_code, {{"__name__": "__main__", "__file__": _target}})
'''
    wrapper_name = "_autodev_input_wrapper.py"
    wrapper_path = os.path.join(workspace_dir, wrapper_name)
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(wrapper)
    return wrapper_name


def _build_execution_env(traits: dict) -> dict:
    """Build subprocess environment with smart overrides for detected traits."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

    if traits.get("has_matplotlib"):
        env["MPLBACKEND"] = "Agg"

    if traits.get("has_pygame"):
        # Allow pygame to initialise without a real display
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

    if traits.get("has_cv2"):
        # Prevent OpenCV from trying to open display windows
        env["DISPLAY"] = env.get("DISPLAY", ":0")

    if traits.get("has_turtle") or traits.get("has_gui"):
        # Ensure DISPLAY is set for Tk-based programs
        env["DISPLAY"] = env.get("DISPLAY", ":0")

    return env


# ─────────────────────────────────────────────────────────────
# Main Execution Entry Point
# ─────────────────────────────────────────────────────────────

def execute_project(
    files: dict[str, str],
    entrypoint: str,
    output_type: str,
    workspace_dir: str,
    llm=None,
    output_category: str = "cli_output",
    execution_command: str = "",
    expected_output_files: list[str] | None = None,
) -> dict:
    """
    Execute the project in the workspace. Returns a structured execution report.
    
    v4: Uses intelligent execution strategy:
    1. Try explicit execution_command from plan (if provided)
    2. Try LLM-resolved command
    3. Fall back to deterministic strategy
    """
    saved_paths = save_files_to_workspace(files, workspace_dir)
    entrypoint_path = os.path.join(workspace_dir, entrypoint)

    if not os.path.isfile(entrypoint_path):
        # Try to find the file with case-insensitive match
        found = _find_entrypoint(workspace_dir, entrypoint)
        if found:
            entrypoint_path = found
            entrypoint = os.path.relpath(found, workspace_dir)
        else:
            return _report(
                success=False, exit_code=-1,
                stdout="", stderr=f"Entrypoint file not found: {entrypoint}",
                runtime=0, error_type="file_type_mismatch",
                error_summary=f"Entrypoint '{entrypoint}' does not exist in workspace",
                files_executed=[], method="none",
            )

    print(f"  [Executor] Running {entrypoint} (type={output_type}, category={output_category}) in {workspace_dir}")

    # Take file snapshot before execution (for detecting generated files)
    pre_snapshot = _snapshot_workspace_files(workspace_dir)

    # ── Smart program trait detection ────────────────────
    traits = _detect_program_traits(files)
    enhanced_env = _build_execution_env(traits)
    if traits["recommended_category"] and traits["recommended_category"] != output_category:
        print(f"  [Executor] Category override: {output_category} → {traits['recommended_category']} (from code analysis)")
        output_category = traits["recommended_category"]

    # Log all detected traits for debugging
    active_traits = [k for k, v in traits.items() if v and k != "recommended_category"]
    if active_traits:
        print(f"  [Executor] Detected traits: {active_traits}")

    # ── Derive timeout strategy from output_category ──────
    is_persistent = output_category in (
        OutputCategory.SERVER_OUTPUT.value,
        OutputCategory.GUI_OUTPUT.value,
    )
    category_timeout = Config.EXECUTOR_TIMEOUT_SERVER if is_persistent else Config.EXECUTOR_TIMEOUT_CLI

    # ── Smart wrapper selection (priority order) ──────────
    # Each wrapper intercepts display calls and saves to files instead.
    # Only ONE wrapper is used (the highest priority match).
    wrapper_name = None
    wrapper_method = None

    if traits["needs_pygame_wrapper"]:
        wrapper_name = _create_pygame_wrapper(workspace_dir, entrypoint)
        wrapper_method = "pygame_wrapper"
        category_timeout = 12  # Games need a bit longer to render frames
    elif traits["needs_turtle_wrapper"]:
        wrapper_name = _create_turtle_wrapper(workspace_dir, entrypoint)
        wrapper_method = "turtle_wrapper"
    elif traits["needs_matplotlib_wrapper"]:
        wrapper_name = _create_matplotlib_wrapper(workspace_dir, entrypoint)
        wrapper_method = "matplotlib_wrapper"
    elif traits["needs_plotly_wrapper"]:
        wrapper_name = _create_plotly_wrapper(workspace_dir, entrypoint)
        wrapper_method = "plotly_wrapper"
    elif traits["needs_pillow_wrapper"]:
        wrapper_name = _create_pillow_wrapper(workspace_dir, entrypoint)
        wrapper_method = "pillow_wrapper"
    elif traits["needs_cv2_wrapper"]:
        wrapper_name = _create_cv2_wrapper(workspace_dir, entrypoint)
        wrapper_method = "cv2_wrapper"
    elif traits["needs_input_wrapper"]:
        wrapper_name = _create_input_wrapper(workspace_dir, entrypoint)
        wrapper_method = "input_wrapper"

    if wrapper_name:
        python_path = get_venv_python()
        wrapper_path = os.path.join(workspace_dir, wrapper_name)
        print(f"  [Executor] Using {wrapper_method} for {entrypoint}")
        report = _run_command(
            f"'{python_path}' '{wrapper_path}'",
            workspace_dir, entrypoint, output_type, output_category,
            wrapper_method,
            timeout=category_timeout,
            timeout_is_success=False,  # Wrappers auto-exit, so timeout = failure
            env=enhanced_env,
        )
        report = _post_process_report(report, workspace_dir, pre_snapshot,
                                       output_category, expected_output_files, entrypoint_path)
        return report

    # ── Strategy 1: Explicit execution command from plan ──
    if execution_command:
        print(f"  [Executor] Using plan execution_command: {execution_command}")
        print(f"  [Executor] timeout_is_success={is_persistent} (category={output_category})")
        report = _run_command(
            execution_command, workspace_dir, entrypoint,
            output_type, output_category, "plan_command",
            timeout=category_timeout,
            timeout_is_success=is_persistent,
            env=enhanced_env,
        )
        report = _post_process_report(report, workspace_dir, pre_snapshot,
                                       output_category, expected_output_files, entrypoint_path)
        if report["success"]:
            return report
        # Only skip fallback strategies for non-recoverable errors
        if report["error_type"] in ("syntax", "import", "compilation", "file_type_mismatch"):
            return report

    # ── Strategy 2: LLM-resolved command ──────────────────
    if llm is not None:
        llm_report = _execute_with_llm(
            entrypoint, entrypoint_path, files,
            output_type, output_category, workspace_dir, llm,
            env=enhanced_env,
        )
        llm_report = _post_process_report(
            llm_report, workspace_dir, pre_snapshot,
            output_category, expected_output_files, entrypoint_path,
        )
        if llm_report["success"] or llm_report.get("error_type") not in ("runtime", "unknown"):
            return llm_report

    # ── Strategy 3: Deterministic fallback ────────────────
    python_path = get_venv_python()
    strategy = get_execution_strategy(
        entrypoint, output_type, output_type,
        workspace_dir, python_path, files,
    )

    if output_type == "html":
        report = _execute_html(entrypoint_path, files, workspace_dir)
    elif strategy["needs_compilation"]:
        report = _execute_compiled(strategy, workspace_dir, entrypoint)
    else:
        report = _run_command(
            strategy["run_cmd"], workspace_dir, entrypoint,
            output_type, output_category, strategy["description"],
            timeout=strategy["timeout"],
            timeout_is_success=strategy["timeout_is_success"],
            env=enhanced_env,
        )

    report = _post_process_report(
        report, workspace_dir, pre_snapshot,
        output_category, expected_output_files, entrypoint_path,
    )
    return report


def _find_entrypoint(workspace_dir: str, entrypoint: str) -> str | None:
    """Try to find entrypoint with case-insensitive or partial match."""
    target = entrypoint.lower()
    for root, dirs, files in os.walk(workspace_dir):
        for f in files:
            if f.lower() == target:
                return os.path.join(root, f)
    return None


# ─────────────────────────────────────────────────────────────
# Post-Processing: File Output Detection
# ─────────────────────────────────────────────────────────────

def _post_process_report(
    report: dict,
    workspace_dir: str,
    pre_snapshot: set[str],
    output_category: str,
    expected_output_files: list[str] | None,
    entrypoint_path: str,
) -> dict:
    """
    Post-process execution report to detect file outputs and adjust success.
    
    This is the KEY FIX for image-generating programs:
    - Detects files created during execution
    - If output_category is file_output and files were created → success
    - Includes generated files in the report
    """
    # Detect newly created files
    new_files = _detect_new_files(workspace_dir, pre_snapshot)
    if new_files:
        report["generated_output_files"] = new_files
        file_names = [os.path.basename(f) for f in new_files]
        report["output"] = report.get("output", "") + \
            f"\n\n[Generated Files] {', '.join(file_names)}"
        print(f"  [Executor] Generated files detected: {file_names}")

    # Check expected output files
    if expected_output_files:
        found = []
        missing = []
        for ef in expected_output_files:
            ef_path = os.path.join(workspace_dir, ef)
            if os.path.isfile(ef_path):
                found.append(ef)
            else:
                missing.append(ef)
        report["expected_files_found"] = found
        report["expected_files_missing"] = missing
        if found:
            report["output"] = report.get("output", "") + \
                f"\n[Expected Files Found] {', '.join(found)}"

    # ── Output Category-Aware Success Adjustment ──────────
    exit_code = report.get("exit_code", -1)

    if output_category == OutputCategory.FILE_OUTPUT.value:
        # For file-output programs: success if files were generated
        if new_files or (expected_output_files and report.get("expected_files_found")):
            if not report["success"]:
                # Program might have exited with non-zero but still produced output
                if report.get("error_type") not in ("syntax", "import"):
                    report["success"] = True
                    report["error_type"] = "none"
                    report["error_summary"] = ""
                    print("  [Executor] File output detected — marking as success")
            # v4.2: Even if already "success", clean up error_summary if files produced
            elif exit_code == 0 and report.get("error_summary"):
                report["error_type"] = "none"
                report["error_summary"] = ""

    elif output_category == OutputCategory.GUI_OUTPUT.value:
        # GUI programs: timeout means success
        pass  # Already handled by timeout_is_success

    elif output_category == OutputCategory.SERVER_OUTPUT.value:
        # Server programs: timeout means success
        pass  # Already handled by timeout_is_success

    elif output_category == OutputCategory.BROWSER_OUTPUT.value:
        # HTML: capture screenshot
        if report["success"] and not report.get("visual_preview"):
            screenshot_path = os.path.join(workspace_dir, "screenshot.png")
            preview = _capture_screenshot(f"file://{entrypoint_path}", screenshot_path)
            if preview:
                report["visual_preview"] = preview

    # v4.2: CLI programs that exit 0 with meaningful stdout should be success
    # even if stderr has warnings (urllib3, deprecation, etc.)
    if not report["success"] and exit_code == 0:
        stdout = report.get("output", "") or report.get("stdout", "")
        if stdout.strip() and stdout.strip() != "(no output)":
            report["success"] = True
            report["error_type"] = "none"
            report["error_summary"] = ""
            print("  [Executor] Exit 0 + meaningful stdout — marking as success")

    # Fix the "(no output)" problem: if we have generated files, don't say no output
    if report.get("output", "").strip() in ("", "(no output)") and new_files:
        report["output"] = f"Program completed. Generated files: {', '.join(os.path.basename(f) for f in new_files)}"

    # Pass the effective output_category back so the graph state is updated
    report["effective_output_category"] = output_category

    return report


# ─────────────────────────────────────────────────────────────
# LLM Command Resolution
# ─────────────────────────────────────────────────────────────

def _execute_with_llm(
    entrypoint: str, filepath: str, files: dict,
    output_type: str, output_category: str,
    workspace_dir: str, llm,
    env: dict | None = None,
) -> dict:
    """Use LLM to determine the best execution command."""
    python_path = get_venv_python()
    
    # Build a concise file content summary for the LLM
    file_summaries = []
    for name, content in list(files.items())[:5]:
        first_lines = "\n".join(content.split("\n")[:10])
        file_summaries.append(f"  {name}: {first_lines[:200]}")
    file_info = "\n".join(file_summaries)

    sys_prompt = f"""You are an execution environment expert. Determine the EXACT shell command to run this project.

RULES:
- For Python projects, use this venv Python: {python_path}
- For Streamlit: '{python_path} -m streamlit run <file> --server.headless=true'
- For C/C++: compile first, then run (e.g., 'gcc main.c -o main -lm && ./main')
- For Java: 'javac <file> && java -cp . <ClassName>'
- For Go: 'go run <file>'
- For Node.js: 'node <file>'
- For programs that save output to files (images, CSVs), just run them normally
- Target OS: {platform.system()}
- Working directory: {workspace_dir}

Output ONLY a JSON object: {{ "command": "<exact shell command>" }}
No markdown, no explanation."""

    prompt = (
        f"Entrypoint: {entrypoint}\n"
        f"Output Type: {output_type}\n"
        f"Output Category: {output_category}\n"
        f"Files in project: {list(files.keys())}\n"
        f"File previews:\n{file_info}"
    )

    try:
        raw = invoke_llm(llm, [
            SystemMessage(content=sys_prompt),
            HumanMessage(content=prompt),
        ])

        json_match = re.search(r"\{.*?\}", raw, re.DOTALL)
        if json_match:
            cmd = json.loads(json_match.group(0))["command"]
        else:
            cmd = json.loads(raw)["command"]

        print(f"  [Executor] LLM Command: {cmd}")

        # Determine timeout strategy based on output_category
        timeout_is_success = output_category in (
            OutputCategory.SERVER_OUTPUT.value,
            OutputCategory.GUI_OUTPUT.value,
        )
        timeout = Config.EXECUTOR_TIMEOUT_SERVER if timeout_is_success else Config.EXECUTOR_TIMEOUT_CLI

        return _run_command(
            cmd, workspace_dir, entrypoint,
            output_type, output_category,
            f"llm:{cmd}",
            timeout=timeout,
            timeout_is_success=timeout_is_success,
            env=env,
        )

    except Exception as e:
        print(f"  [Executor] LLM execution planning failed: {e}")
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr=f"LLM command resolution failed: {e}",
            runtime=0, error_type="runtime",
            error_summary=f"LLM command resolution failed: {e}",
            files_executed=[entrypoint], method="llm_failed",
        )


# ─────────────────────────────────────────────────────────────
# Generic Command Runner
# ─────────────────────────────────────────────────────────────

def _run_command(
    cmd: str,
    workspace_dir: str,
    entrypoint: str,
    output_type: str,
    output_category: str,
    method: str,
    timeout: int = 45,
    timeout_is_success: bool = False,
    env: dict | None = None,
) -> dict:
    """Execute a shell command and return a structured report."""
    if not cmd:
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr="No command to execute",
            runtime=0, error_type="runtime",
            error_summary="No executable command",
            files_executed=[entrypoint], method=method,
        )

    start = time.time()
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, cwd=workspace_dir,
            shell=True, stdin=subprocess.DEVNULL,
            env=env or {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        runtime = time.time() - start
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        success = r.returncode == 0

        error_type, error_summary = "none", ""
        if not success:
            error_type, error_summary = _classify_error(stderr, output_type)

        return _report(
            success=success, exit_code=r.returncode,
            stdout=stdout, stderr=stderr,
            output=_combine_output(stdout, stderr),
            runtime=runtime, error_type=error_type,
            error_summary=error_summary,
            files_executed=[entrypoint], method=method,
        )

    except subprocess.TimeoutExpired as e:
        stdout = ""
        stderr = ""
        if hasattr(e, "stdout") and e.stdout:
            stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else str(e.stdout)
        if hasattr(e, "stderr") and e.stderr:
            stderr = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e.stderr)

        if timeout_is_success:
            # For GUI/server programs, timeout means the program is running — that's success
            return _report(
                success=True, exit_code=0,
                stdout=stdout, stderr=stderr,
                output=_combine_output(stdout, stderr) or f"Program running (timeout after {timeout}s — expected for {output_category})",
                runtime=timeout, error_type="none", error_summary="",
                files_executed=[entrypoint],
                method=f"{method} (timeout=success)",
            )
        else:
            return _report(
                success=False, exit_code=-1,
                stdout=stdout, stderr=stderr,
                output=_combine_output(stdout, stderr),
                runtime=timeout, error_type="timeout",
                error_summary=f"Execution timed out after {timeout}s",
                files_executed=[entrypoint], method=method,
            )

    except Exception as e:
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr=str(e),
            runtime=time.time() - start, error_type="runtime",
            error_summary=str(e),
            files_executed=[entrypoint], method=method,
        )


# ─────────────────────────────────────────────────────────────
# Compiled Language Executor
# ─────────────────────────────────────────────────────────────

def _execute_compiled(strategy: dict, workspace_dir: str, entrypoint: str) -> dict:
    """Execute a compiled language: compile first, then run."""
    compile_cmd = strategy["compile_cmd"]
    run_cmd = strategy["run_cmd"]
    description = strategy["description"]

    # Step 1: Compile
    print(f"  [Executor] Compiling: {compile_cmd}")
    start = time.time()
    try:
        comp = subprocess.run(
            compile_cmd, capture_output=True, text=True,
            timeout=Config.EXECUTOR_TIMEOUT_COMPILATION,
            cwd=workspace_dir, shell=True,
        )
        if comp.returncode != 0:
            return _report(
                success=False, exit_code=comp.returncode,
                stdout=comp.stdout.strip(), stderr=comp.stderr.strip(),
                output=_combine_output(comp.stdout.strip(), comp.stderr.strip()),
                runtime=time.time() - start,
                error_type="compilation",
                error_summary=f"Compilation failed: {comp.stderr.strip().split(chr(10))[-1][:200]}",
                files_executed=[entrypoint],
                method=f"{description} (compile)",
            )
    except subprocess.TimeoutExpired:
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr="Compilation timed out",
            runtime=Config.EXECUTOR_TIMEOUT_COMPILATION,
            error_type="compilation",
            error_summary="Compilation timed out",
            files_executed=[entrypoint],
            method=f"{description} (compile timeout)",
        )
    except Exception as e:
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr=str(e),
            runtime=time.time() - start,
            error_type="compilation",
            error_summary=f"Compilation error: {e}",
            files_executed=[entrypoint],
            method=f"{description} (compile error)",
        )

    compile_time = time.time() - start
    print(f"  [Executor] Compilation OK ({compile_time:.1f}s). Running: {run_cmd}")

    # Step 2: Run
    run_start = time.time()
    try:
        r = subprocess.run(
            run_cmd, capture_output=True, text=True,
            timeout=strategy["timeout"],
            cwd=workspace_dir, shell=True,
            stdin=subprocess.DEVNULL,
        )
        runtime = time.time() - run_start
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        success = r.returncode == 0

        error_type, error_summary = "none", ""
        if not success:
            error_type, error_summary = _classify_error(stderr, "compiled")

        return _report(
            success=success, exit_code=r.returncode,
            stdout=stdout, stderr=stderr,
            output=_combine_output(stdout, stderr),
            runtime=compile_time + runtime,
            error_type=error_type, error_summary=error_summary,
            files_executed=[entrypoint],
            method=f"{description} (compiled + ran)",
        )
    except subprocess.TimeoutExpired as e:
        stdout = ""
        if hasattr(e, "stdout") and e.stdout:
            stdout = e.stdout.decode() if isinstance(e.stdout, bytes) else str(e.stdout)
        if strategy["timeout_is_success"]:
            return _report(
                success=True, exit_code=0,
                stdout=stdout, stderr="",
                runtime=strategy["timeout"],
                error_type="none", error_summary="",
                files_executed=[entrypoint],
                method=f"{description} (running)",
            )
        return _report(
            success=False, exit_code=-1,
            stdout=stdout, stderr="Execution timed out",
            runtime=strategy["timeout"],
            error_type="timeout",
            error_summary="Runtime execution timed out",
            files_executed=[entrypoint],
            method=f"{description} (timeout)",
        )
    except Exception as e:
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr=str(e),
            runtime=time.time() - run_start,
            error_type="runtime", error_summary=str(e),
            files_executed=[entrypoint],
            method=f"{description} (run error)",
        )


# ─────────────────────────────────────────────────────────────
# HTML Executor (validation + screenshot)
# ─────────────────────────────────────────────────────────────

def _execute_html(filepath: str, files: dict[str, str], workspace_dir: str) -> dict:
    """Validate an HTML file. Check for proper structure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return _report(
            success=False, exit_code=-1,
            stdout="", stderr=f"Could not read HTML file: {e}",
            runtime=0, error_type="runtime",
            error_summary=f"File read error: {e}",
            files_executed=[os.path.basename(filepath)],
            method="html_validation",
        )

    content_lower = content.lower()
    issues = []

    if "<!doctype" not in content_lower and "<html" not in content_lower:
        issues.append("Missing <!DOCTYPE html> or <html> tag")
    if "<head" not in content_lower:
        issues.append("Missing <head> section")
    if "<body" not in content_lower:
        issues.append("Missing <body> section")
    if "</html>" not in content_lower:
        issues.append("Missing closing </html> tag")

    # Check if linked CSS/JS files exist
    for name in files:
        if name.endswith(".css") and name not in content and f'href="{name}"' not in content:
            issues.append(f"CSS file '{name}' exists but may not be linked in HTML")
        if name.endswith(".js") and name not in content and f'src="{name}"' not in content:
            issues.append(f"JS file '{name}' exists but may not be linked in HTML")

    if issues:
        return _report(
            success=False, exit_code=1,
            stdout="", stderr="HTML validation issues:\n" + "\n".join(f"- {i}" for i in issues),
            runtime=0, error_type="syntax",
            error_summary=issues[0],
            files_executed=[os.path.basename(filepath)],
            method="html_validation",
        )

    screenshot_path = os.path.join(workspace_dir, "screenshot.png")
    preview = _capture_screenshot(f"file://{filepath}", screenshot_path)

    return _report(
        success=True, exit_code=0,
        stdout=f"HTML validated OK ({len(content)} chars, {len(files)} project files).",
        stderr="",
        runtime=0, error_type="none", error_summary="",
        files_executed=[os.path.basename(filepath)],
        method="html_validation",
        visual_preview=preview,
    )


# ─────────────────────────────────────────────────────────────
# File Launching (for UI "Open" / "Launch" buttons)
# ─────────────────────────────────────────────────────────────

def launch_file(filepath: str) -> bool:
    """Launch a file natively: HTML in browser, Python in terminal, etc."""
    if not os.path.isfile(filepath):
        return False
    ext = os.path.splitext(filepath)[1].lower()
    abs_path = os.path.abspath(filepath)
    system = platform.system()

    try:
        if ext == ".html":
            webbrowser.open(f"file://{abs_path}")
            return True

        if ext == ".py":
            python_path = get_venv_python()
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                src = f.read(500)
            is_streamlit = "import streamlit" in src or "from streamlit" in src

            if is_streamlit:
                cmd = f"'{python_path}' -m streamlit run '{abs_path}'"
            else:
                cmd = f"'{python_path}' '{abs_path}'"

            if system == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell application \"Terminal\"", "-e", "activate", "-e", f"do script \"{cmd}\"", "-e", "end tell"])
            elif system == "Windows":
                subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", cmd])
            else:
                subprocess.Popen(["x-terminal-emulator", "-e", cmd])
            return True

        if ext in (".sh", ".bash", ".zsh"):
            os.chmod(abs_path, 0o755)
            if system == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell application \"Terminal\"", "-e", "activate", "-e", f"do script \"/bin/bash '{abs_path}'\"", "-e", "end tell"])
            else:
                subprocess.Popen(["/bin/bash", abs_path])
            return True

        if ext in (".js", ".mjs"):
            if system == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell application \"Terminal\"", "-e", "activate", "-e", f"do script \"node '{abs_path}'\"", "-e", "end tell"])
            else:
                subprocess.Popen(["node", abs_path])
            return True

        if ext in (".c", ".cpp", ".cc", ".java", ".go", ".rs"):
            # Compiled: open terminal and compile+run
            strategy = get_execution_strategy(
                os.path.basename(filepath), "", "",
                os.path.dirname(abs_path), get_venv_python(),
            )
            if strategy["compile_cmd"]:
                cmd = f"{strategy['compile_cmd']} && {strategy['run_cmd']}"
            else:
                cmd = strategy["run_cmd"]
            if system == "Darwin":
                subprocess.Popen(["osascript", "-e", "tell application \"Terminal\"", "-e", "activate", "-e", f"do script \"{cmd}\"", "-e", "end tell"])
            return True

    except Exception as e:
        print(f"[Launch] Error: {e}")
    return False


# ─────────────────────────────────────────────────────────────
# Screenshot Native Capture
# ─────────────────────────────────────────────────────────────

def _capture_screenshot(url: str, out_path: str) -> str:
    """Capture a screenshot using available browser (Chrome or Safari)."""
    # Try Chrome first
    chrome_paths = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
    ]
    for chrome_path in chrome_paths:
        if os.path.exists(chrome_path):
            try:
                subprocess.run(
                    [chrome_path, "--headless", f"--screenshot={out_path}",
                     "--window-size=1280,800", "--disable-gpu", url],
                    capture_output=True, timeout=10,
                )
                if os.path.isfile(out_path):
                    return out_path
            except Exception as e:
                print(f"  [Screenshot] Chrome failed: {e}")

    # No browser available for screenshots
    print("  [Screenshot] No headless browser available")
    return ""


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _combine_output(stdout: str, stderr: str) -> str:
    """Combine stdout and stderr into a single display string."""
    parts = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(("STDERR:\n" if stdout else "") + stderr)
    return "\n".join(parts).strip() or "(no output)"


def _classify_error(stderr: str, output_type: str) -> tuple[str, str]:
    """Classify an error from stderr. Returns (error_type, summary)."""
    if not stderr:
        return "runtime", "Non-zero exit code"

    stderr_lower = stderr.lower()
    last_line = stderr.strip().split("\n")[-1].strip()

    # Python errors
    if "syntaxerror" in stderr_lower or "indentationerror" in stderr_lower:
        return "syntax", last_line
    if "modulenotfounderror" in stderr_lower or "no module named" in stderr_lower:
        return "import", last_line
    if "importerror" in stderr_lower:
        return "import", last_line

    # Compilation errors
    if any(x in stderr_lower for x in ["error:", "undefined reference", "undeclared identifier"]):
        if output_type in ("c", "cpp", "java", "rust", "go", "compiled"):
            return "compilation", last_line

    # General runtime errors
    for err in ["typeerror", "valueerror", "keyerror", "attributeerror",
                "nameerror", "zerodivisionerror", "filenotfounderror",
                "permissionerror", "oserror"]:
        if err in stderr_lower:
            return "runtime", last_line

    return "runtime", last_line


def _report(
    success: bool, exit_code: int,
    stdout: str = "", stderr: str = "", output: str = "",
    runtime: float = 0, error_type: str = "none",
    error_summary: str = "", files_executed: list[str] | None = None,
    method: str = "", visual_preview: str = "",
) -> dict:
    """Build a structured execution report."""
    return {
        "success": success,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "output": output or _combine_output(stdout, stderr),
        "runtime_seconds": round(runtime, 2),
        "error_type": error_type,
        "error_summary": error_summary,
        "files_executed": files_executed or [],
        "execution_method": method,
        "visual_preview": visual_preview,
        "generated_output_files": [],  # Populated by _post_process_report
    }

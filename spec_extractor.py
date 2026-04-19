"""
spec_extractor.py — AutoDev v4 Spec Agent.

v4 changes:
- Added output_category and expected_output_files to spec schema
- Expanded output_type examples to include C, C++, Java, Go, Rust, etc.
- Better fallback handling for new language types
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from .contracts import SpecContract
from .llm_utils import invoke_llm, validate_llm_json
from .config import Config
from .context_builder import build_spec_context


# ─────────────────────────────────────────────────────────────
# Spec Schema
# ─────────────────────────────────────────────────────────────

_SPEC_SCHEMA = """\
{
  "problem_statement": "Clear problem statement (1-2 sentences)",
  "objective": "What the solution achieves",
  "output_type": "python|html|streamlit|shell|js|typescript|c|cpp|java|go|rust|ruby|php|r|kotlin|swift|other",
  "expected_files": ["main.py"],
  "entrypoint": "main.py",
  "dependencies": ["package1", "package2"],
  "execution_target": "terminal|browser|streamlit|gui",
  "acceptance_criteria": ["Criteria 1", "Criteria 2"],
  "constraints": ["No external APIs"],
  "project_type": "script|web_app|cli_tool|gui_app|library|data_pipeline|game",
  "output_category": "cli_output|file_output|gui_output|server_output|browser_output|no_output",
  "expected_output_files": ["output.png", "result.csv"]
}"""

_SYS_SPEC = f"""\
<role>Spec Agent — Requirements Engineer</role>
<mission>Convert a user's raw request into a precise, immutable SpecContract.</mission>

<rules>
  - Infer output_type from the prompt:
      Python scripts/tools → python
      Web pages/apps → html
      Streamlit dashboards → streamlit
      Shell/bash scripts → shell
      Node.js/JS → js
      C programs → c
      C++ programs → cpp
      Java programs → java
      Go programs → go
      Rust programs → rust
      Others → choose most specific or "other"
  - Pick a SINGLE entrypoint file (main.py, index.html, main.c, Main.java, etc.)
  - expected_files: all files needed. Prefer FEWER files unless complexity requires it.
  - dependencies: list pip/npm packages. Omit standard library modules.
  - execution_target: terminal (most scripts), browser (HTML), streamlit, gui (tkinter/PyQt)
  - output_category: CRITICAL for correct validation. Choose carefully:
      cli_output → program prints to stdout/stderr (default for text-only programs)
      file_output → program generates files (images, CSVs, PDFs, charts, etc.)
      gui_output → program opens a persistent GUI window (tkinter, PyQt, PySide, wxPython)
      server_output → program runs a server (Flask, FastAPI, Django, Streamlit)
      browser_output → static HTML to view in browser
      no_output → script with side effects only
  - expected_output_files: list files the program will CREATE (e.g. ["chart.png", "data.csv"])
      Leave empty if program only prints to terminal.

  CRITICAL — OUTPUT CATEGORY RULES:
  - Pygame games → ALWAYS set output_category to "file_output" (not "gui_output")
    Our system auto-captures pygame screenshots for validation.
  - Turtle graphics → ALWAYS set output_category to "file_output" (not "gui_output")
    Our system auto-captures turtle canvas as image files.
  - matplotlib/seaborn charts → ALWAYS set output_category to "file_output"
    and list expected files. Our wrapper auto-saves figures.
  - Plotly charts → ALWAYS set output_category to "file_output"
  - PIL/Pillow image generation → ALWAYS set output_category to "file_output"
  - OpenCV (cv2) programs → ALWAYS set output_category to "file_output"
  - Programs that use input() → still set their natural category;
    our system auto-provides mock input.
  - tkinter/PyQt/PySide GUI apps → set output_category to "gui_output"
  - Only use "gui_output" for PERSISTENT window apps (tkinter, PyQt, PySide, wxPython)
    NOT for pygame or turtle.

  - acceptance_criteria: measurable success conditions
  - Do NOT over-complicate. Use fewest files possible.
</rules>

<output>Return ONLY valid JSON matching the schema. No markdown fences, no explanation.</output>
<schema>{_SPEC_SCHEMA}</schema>"""


def extract_spec(task: str, llm, refined_prompt: str = "") -> tuple[dict, str | None]:
    """
    Extract a SpecContract from the raw task.
    Returns (spec_dict, error_or_None).
    """
    prompt = build_spec_context(refined_prompt or task)

    raw = invoke_llm(llm, [
        SystemMessage(content=_SYS_SPEC),
        HumanMessage(content=prompt),
    ])

    parsed, error = validate_llm_json(
        raw,
        required_keys=Config.SPEC_REQUIRED_KEYS,
        llm=llm,
        schema_description=_SPEC_SCHEMA,
        max_repair_attempts=2,
    )

    if parsed is None:
        print(f"  [SpecAgent] Parse failed — using fallback. Error: {error}")
        parsed = _fallback_spec(refined_prompt or task)
        error = f"Used fallback spec: {error}"

    # Validate and clean
    try:
        contract = SpecContract.from_dict(parsed)
        return contract.to_dict(), None
    except Exception as ve:
        print(f"  [SpecAgent] Contract validation failed: {ve}. Using cleaned dict.")
        # Try direct dict with defaults
        parsed.setdefault("output_type", "python")
        parsed.setdefault("entrypoint", "main.py")
        parsed.setdefault("expected_files", [parsed.get("entrypoint", "main.py")])
        parsed.setdefault("output_category", "cli_output")
        parsed.setdefault("expected_output_files", [])
        return parsed, str(ve)


def _fallback_spec(task: str) -> dict:
    """Build a minimal spec when LLM output is unparseable."""
    task_lower = task.lower()

    # Infer output type from task
    ot = "python"
    et = "main.py"
    exec_target = "terminal"
    output_category = "cli_output"

    if any(w in task_lower for w in ["html", "webpage", "website", "portfolio", "landing page"]):
        ot, et, exec_target = "html", "index.html", "browser"
        output_category = "browser_output"
    elif any(w in task_lower for w in ["streamlit", "dashboard"]):
        ot, et, exec_target = "streamlit", "app.py", "streamlit"
        output_category = "server_output"
    elif any(w in task_lower for w in ["bash", "shell", "script sh"]):
        ot, et, exec_target = "shell", "run.sh", "terminal"
    elif any(w in task_lower for w in ["javascript", "node", " js "]):
        ot, et = "js", "main.js"
    elif any(w in task_lower for w in ["typescript", " ts "]):
        ot, et = "typescript", "main.ts"
    elif any(w in task_lower for w in [" c program", " c code", "in c ", "gcc"]):
        ot, et = "c", "main.c"
    elif any(w in task_lower for w in ["c++", "cpp", "cplusplus"]):
        ot, et = "cpp", "main.cpp"
    elif any(w in task_lower for w in ["java ", "java program", "javac"]):
        ot, et = "java", "Main.java"
    elif any(w in task_lower for w in [" go ", "golang", "go program"]):
        ot, et = "go", "main.go"
    elif any(w in task_lower for w in ["rust", "cargo"]):
        ot, et = "rust", "main.rs"
    elif any(w in task_lower for w in ["tkinter", "pyqt", "pyside", "wxpython"]):
        exec_target = "gui"
        output_category = "gui_output"

    # Check for file-generating tasks (these ALWAYS override to file_output)
    expected_output_files = []

    # Pygame games → file_output (auto-captured screenshots)
    if any(w in task_lower for w in ["pygame", "game with python", "python game"]):
        output_category = "file_output"
        expected_output_files = ["output_final.png"]

    # Turtle graphics → file_output (auto-captured canvas)
    elif any(w in task_lower for w in ["turtle", "turtle graphics"]):
        output_category = "file_output"
        expected_output_files = ["output_turtle.png"]

    # Matplotlib / Seaborn / plotting
    elif any(w in task_lower for w in [
        "matplotlib", "seaborn", "plot", "chart", "graph",
        "histogram", "scatter", "bar chart", "pie chart",
        "line chart", "heatmap", "visualization",
    ]):
        output_category = "file_output"
        expected_output_files = ["output_1.png"]

    # Plotly
    elif any(w in task_lower for w in ["plotly", "interactive chart", "interactive plot"]):
        output_category = "file_output"
        expected_output_files = ["output_1.html"]

    # PIL / Pillow / image generation
    elif any(w in task_lower for w in [
        "pillow", "pil", "create image", "generate image",
        "image processing", "fractal", "mandelbrot",
    ]):
        output_category = "file_output"
        expected_output_files = ["output_1.png"]

    # OpenCV
    elif any(w in task_lower for w in ["opencv", "cv2", "computer vision", "image detection"]):
        output_category = "file_output"
        expected_output_files = ["output_cv_1.png"]

    # Generic file-saving tasks
    elif any(w in task_lower for w in [
        "save", "savefig", "export", "create pdf",
        "csv", "generate file",
    ]):
        output_category = "file_output"
        if "png" in task_lower or "image" in task_lower:
            expected_output_files = ["output.png"]
        elif "csv" in task_lower:
            expected_output_files = ["output.csv"]
        elif "pdf" in task_lower:
            expected_output_files = ["output.pdf"]

    return {
        "problem_statement": task[:500],
        "objective": f"Build: {task[:200]}",
        "output_type": ot,
        "expected_files": [et],
        "entrypoint": et,
        "dependencies": [],
        "execution_target": exec_target,
        "acceptance_criteria": [f"Complete the requested task: {task[:200]}"],
        "constraints": [],
        "project_type": "script",
        "output_category": output_category,
        "expected_output_files": expected_output_files,
    }

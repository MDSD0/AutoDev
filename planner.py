"""
planner.py — AutoDev v4 Planner Agent.

v4 changes:
- Updated runtime types to support all languages
- Added execution_command to plan schema
- Better fallback for new language types
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from .contracts import PlanContract
from .llm_utils import invoke_llm, validate_llm_json
from .config import Config
from .context_builder import build_planner_context


# ─────────────────────────────────────────────────────────────
# Plan Schema
# ─────────────────────────────────────────────────────────────

_PLAN_SCHEMA = """\
{
  "project_structure": {
    "main.py": "Entry point — main logic"
  },
  "file_order": ["main.py"],
  "packages": ["requests"],
  "runtime": "python3|node|bash|browser|c|cpp|java|go|rust|ruby|php|r|kotlin|swift|typescript|other",
  "entrypoint": "main.py",
  "validation_strategy": "Run entrypoint and check for errors",
  "test_strategy": "Syntax check all files",
  "fallback_notes": "",
  "execution_command": "python3 main.py"
}"""

_SYS_PLANNER = f"""\
<role>Planner Agent — Architect</role>
<mission>Convert the SpecContract into a concrete, dependency-ordered PlanContract.</mission>

<rules>
  - project_structure: map EVERY file to its purpose
  - file_order: dependency order (utilities first, entrypoint last)
  - packages: ONLY external packages (not stdlib). Use correct pip/npm names:
      PIL → pillow, cv2 → opencv-python, yaml → pyyaml, bs4 → beautifulsoup4
  - runtime: choose the correct runtime for the specified output_type
  - entrypoint: MUST match spec.entrypoint
  - execution_command: the EXACT command to run this project (IMPORTANT!)
      For Python: "python3 main.py"
      For Streamlit: "streamlit run app.py --server.headless=true"
      For C: "gcc main.c -o main -lm && ./main"
      For Java: "javac Main.java && java -cp . Main"
      For Go: "go run main.go"
      For Node.js: "node main.js"
      For HTML: "" (empty, will be opened in browser)
  - Use FEWEST files possible. Don't over-engineer.
    A simple Python task → 1 file.
    A web app → 2-3 files (HTML, CSS, JS).
    Multi-module only when genuinely needed.
  - DO NOT include test files unless explicitly requested.
</rules>

<output>Return ONLY valid JSON. No markdown, no explanation.</output>
<schema>{_PLAN_SCHEMA}</schema>"""


def create_plan(spec: dict, llm, rag_context: str = "") -> tuple[dict, str | None]:
    """
    Create a PlanContract from the SpecContract.
    Returns (plan_dict, error_or_None).
    """
    ctx = build_planner_context(spec)
    if rag_context:
        ctx += f"\n\nRelevant context from memory:\n{rag_context[:1500]}"

    raw = invoke_llm(llm, [
        SystemMessage(content=_SYS_PLANNER),
        HumanMessage(content=ctx),
    ])

    parsed, error = validate_llm_json(
        raw,
        required_keys=Config.PLAN_REQUIRED_KEYS,
        llm=llm,
        schema_description=_PLAN_SCHEMA,
        max_repair_attempts=2,
    )

    if parsed is None:
        print(f"  [PlannerAgent] Parse failed, using fallback. Error: {error}")
        parsed = _fallback_plan(spec)
        error = f"Used fallback plan: {error}"

    # Validate and clean
    try:
        contract = PlanContract.from_dict(parsed)
        return contract.to_dict(), None
    except Exception as ve:
        print(f"  [PlannerAgent] Validation failed: {ve}. Using cleaned dict.")
        parsed.setdefault("project_structure", {spec.get("entrypoint", "main.py"): "Main file"})
        parsed.setdefault("file_order", [spec.get("entrypoint", "main.py")])
        parsed.setdefault("packages", spec.get("dependencies", []))
        parsed.setdefault("entrypoint", spec.get("entrypoint", "main.py"))
        parsed.setdefault("runtime", _infer_runtime(spec.get("output_type", "python")))
        parsed.setdefault("execution_command", "")
        return parsed, str(ve)


def _infer_runtime(output_type: str) -> str:
    """Map output_type to runtime."""
    mapping = {
        "python": "python3",
        "streamlit": "python3",
        "html": "browser",
        "shell": "bash",
        "js": "node",
        "typescript": "typescript",
        "c": "c",
        "cpp": "cpp",
        "java": "java",
        "go": "go",
        "rust": "rust",
        "ruby": "ruby",
        "php": "php",
        "r": "r",
        "kotlin": "kotlin",
        "swift": "swift",
    }
    return mapping.get(output_type, "other")


def _fallback_plan(spec: dict) -> dict:
    """Build a minimal plan when LLM output fails."""
    entrypoint = spec.get("entrypoint", "main.py")
    files = spec.get("expected_files", [entrypoint])
    output_type = spec.get("output_type", "python")
    runtime = _infer_runtime(output_type)

    # Build execution command
    exec_cmd = ""
    if output_type == "python":
        exec_cmd = f"python3 {entrypoint}"
    elif output_type == "streamlit":
        exec_cmd = f"streamlit run {entrypoint} --server.headless=true"
    elif output_type == "c":
        exec_cmd = f"gcc {entrypoint} -o main -lm && ./main"
    elif output_type == "cpp":
        exec_cmd = f"g++ {entrypoint} -o main -std=c++17 && ./main"
    elif output_type == "java":
        class_name = entrypoint.replace(".java", "")
        exec_cmd = f"javac {entrypoint} && java -cp . {class_name}"
    elif output_type == "go":
        exec_cmd = f"go run {entrypoint}"
    elif output_type == "js":
        exec_cmd = f"node {entrypoint}"

    return {
        "project_structure": {f: f"Project file ({f})" for f in files},
        "file_order": files,
        "packages": spec.get("dependencies", []),
        "runtime": runtime,
        "entrypoint": entrypoint,
        "validation_strategy": "Run entrypoint and check for errors",
        "test_strategy": "Syntax check all files",
        "fallback_notes": "Fallback plan (LLM failed)",
        "execution_command": exec_cmd,
    }

"""
file_generator.py — AutoDev v4 Implementer Agent.

v4 changes:
- Increased inter-file context from 800 to 4000 chars
- Added OS context to system prompt
- Force full-regen when same error repeats 2x
- Added code validation pre-check
- System prompt encourages production-quality code
"""
from __future__ import annotations

import json
import platform

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_utils import invoke_llm
from .config import Config
from .context_builder import build_implementer_context


# ─────────────────────────────────────────────────────────────
# System prompt for code generation
# ─────────────────────────────────────────────────────────────

_SYS_IMPLEMENTER = f"""\
<role>Implementer Agent — Code Generator</role>
<mission>Generate production-quality code files for the given specification.</mission>

<environment>
  OS: {platform.system()} {platform.machine()}
  Python: {platform.python_version()}
</environment>

<rules>
  - Write COMPLETE, RUNNABLE code. No placeholders, no TODOs, no "implement here".
  - Every file must be self-contained and importable without errors.
  - Use modern best practices for the target language.
  - For Python:
    - Use proper imports, type hints, and error handling.
    - For GUI apps: use tkinter or PyQt6 with proper mainloop/exec.
    - For file-generating scripts: save output files to the CURRENT DIRECTORY.
  - For matplotlib/seaborn:
    - ALWAYS use Agg backend: import matplotlib; matplotlib.use('Agg')
    - ALWAYS save with plt.savefig('output.png', dpi=150) BEFORE plt.show().
    - ALWAYS include plt.show() at the end (our wrapper will intercept it).
  - For Pygame games:
    - Write the game normally with display.flip()/update() and game loops.
    - Include print() statements that describe the game state.
    - Our execution wrapper auto-captures screenshots for validation.
  - For Turtle graphics:
    - Write the drawing normally. Include turtle.done() or turtle.mainloop() at the end.
    - Our wrapper intercepts done/mainloop and saves the canvas as an image.
  - For PIL/Pillow:
    - ALWAYS save output images to files: img.save('output.png')
    - Do NOT rely on img.show() alone — it opens in external viewer.
  - For Plotly:
    - ALWAYS save to HTML: fig.write_html('output.html')
    - Optionally also call fig.show() (our wrapper intercepts it).
  - For OpenCV (cv2):
    - ALWAYS save outputs with cv2.imwrite('output.png', img)
    - Do NOT rely on cv2.imshow()/waitKey() alone — headless environments crash.
  - For programs that need user input:
    - Provide sensible default values or use argparse with defaults.
    - If input() is unavoidable, keep prompts clear (our system auto-responds).
  - For HTML:
    - Include all CSS and JS inline unless a separate file is planned.
    - Use modern HTML5, responsive design, and clean aesthetics.
  - For C/C++:
    - Include all necessary headers (#include <stdio.h>, <stdlib.h>, etc.)
    - Use standard-compliant code (C11/C++17).
  - For Java:
    - Class name MUST match filename (Main.java → public class Main).
  - DO NOT use sudo, system-wide installs, or network-dependent tests.
  - DO NOT generate test files unless explicitly requested.
  - Keep the code as simple as possible while meeting all requirements.
  - ALWAYS produce visible output: print() for CLI, save files for visual programs.
</rules>

<output>Return ONLY the complete file content. No markdown fences, no explanation.</output>"""


_SYS_PATCHER = f"""\
<role>Implementer Agent — Code Patcher</role>
<mission>Fix the specified files based on the error diagnosis.</mission>

<environment>
  OS: {platform.system()} {platform.machine()}
</environment>

<rules>
  - Return ONLY the COMPLETE fixed file content (not a diff).
  - Fix the root cause, not just the symptom.
  - Do NOT introduce new dependencies unless absolutely necessary.
  - Keep all existing functionality intact.
  - If the error is about missing output: ensure the program produces visible output.
  - For file-generating programs: save files to the current directory (not absolute paths).
  - For matplotlib: use plt.savefig('output.png') BEFORE plt.show(), and use Agg backend.
  - For pygame: write the game normally — our wrapper captures screenshots.
  - For turtle: include turtle.done() at end — our wrapper saves the canvas.
  - For PIL/Pillow: use img.save('output.png') to save images to current directory.
  - For plotly: use fig.write_html('output.html') to save charts.
  - For cv2/OpenCV: use cv2.imwrite('output.png', img), NOT cv2.imshow().
  - For programs with input(): keep prompts clear (our system auto-provides responses).
</rules>

<output>Return ONLY the complete file content. No markdown fences, no explanation.</output>"""


# ─────────────────────────────────────────────────────────────
# Main generator
# ─────────────────────────────────────────────────────────────

def generate_files(
    spec: dict,
    plan: dict,
    llm,
    retry_history: list | None = None,
    current_files: dict | None = None,
    review_feedback: str = "",
    affected_files: list | None = None,
    conversation_context: str = "",
) -> dict[str, str]:
    """
    Generate all files for the project.
    
    On first run: generates all files from scratch.
    On retry: patches only affected files, regenerates if patch fails.
    
    Args:
        conversation_context: Previous conversation turns for context
                              (gives the LLM memory like ChatGPT/Claude).
    """
    file_order = plan.get("file_order", spec.get("expected_files", ["main.py"]))
    project_structure = plan.get("project_structure", {})

    # If retrying with specific affected files, only regenerate those
    if retry_history and affected_files and current_files:
        print(f"  [Implementer] Targeted fix: {affected_files}")
        return _patch_files(
            spec, plan, llm, current_files,
            affected_files, review_feedback, retry_history,
            conversation_context=conversation_context,
        )

    # Full generation
    print(f"  [Implementer] Full generation: {file_order}")
    return _generate_all(
        spec, plan, llm, file_order, project_structure, review_feedback, retry_history,
        conversation_context=conversation_context,
    )


def _generate_all(
    spec: dict, plan: dict, llm,
    file_order: list, project_structure: dict,
    review_feedback: str = "",
    retry_history: list | None = None,
    conversation_context: str = "",
) -> dict[str, str]:
    """Generate all files from scratch."""
    generated: dict[str, str] = {}

    for filename in file_order:
        desc = project_structure.get(filename, f"Project file ({filename})")

        # Build inter-file context (v4: 4000 chars instead of 800)
        prev_ctx = ""
        if generated:
            prev_parts = []
            for prev_name, prev_code in generated.items():
                # Show more context for better cross-file coherence
                preview = prev_code[:4000]
                if len(prev_code) > 4000:
                    preview += "\n# ... [truncated]"
                prev_parts.append(f"--- {prev_name} ---\n{preview}")
            prev_ctx = "\n\n".join(prev_parts)

        ctx = build_implementer_context(spec, plan)

        prompt = f"Generate the file: {filename}\nDescription: {desc}\n\n"
        prompt += f"Project context:\n{ctx}\n\n"

        if conversation_context:
            prompt += f"CONVERSATION HISTORY (previous user requests and responses):\n{conversation_context}\n\n"

        if prev_ctx:
            prompt += f"Other project files already generated:\n{prev_ctx}\n\n"

        if review_feedback:
            prompt += f"IMPORTANT — Fix this issue:\n{review_feedback}\n\n"

        if retry_history:
            recent = retry_history[-2:]
            retry_ctx = "\n".join(
                f"  Attempt #{r.get('attempt')}: [{r.get('error_type', '?')}] {r.get('root_cause', r.get('feedback', ''))}"
                for r in recent
            )
            prompt += f"Previous failures (DO NOT repeat these mistakes):\n{retry_ctx}\n\n"

        prompt += f"Return ONLY the complete content of {filename}. No markdown fences."

        code = invoke_llm(llm, [
            SystemMessage(content=_SYS_IMPLEMENTER),
            HumanMessage(content=prompt),
        ])

        # Clean code: strip markdown fences if LLM included them
        code = _strip_fences(code)

        # Basic validation
        if not code.strip():
            code = f"# Error: Empty file generated for {filename}\n# Regeneration needed"

        generated[filename] = code
        print(f"  [Implementer] Generated: {filename} ({len(code)} chars)")

    return generated


def _patch_files(
    spec: dict, plan: dict, llm,
    current_files: dict,
    affected_files: list,
    review_feedback: str,
    retry_history: list,
    conversation_context: str = "",
) -> dict[str, str]:
    """Patch only the affected files, keep others intact."""
    result = dict(current_files)

    for filename in affected_files:
        if filename not in current_files:
            print(f"  [Implementer] Skipping {filename} (not in current files)")
            continue

        old_code = current_files[filename]

        # Build context with the error info and other files
        other_files_ctx = "\n\n".join(
            f"--- {name} ---\n{code[:3000]}"
            for name, code in current_files.items()
            if name != filename
        )

        recent_errors = retry_history[-3:] if retry_history else []
        error_ctx = "\n".join(
            f"  [{r.get('error_type', '?')}] {r.get('root_cause', r.get('feedback', ''))}"
            for r in recent_errors
        )

        prompt = (
            f"Fix this file: {filename}\n\n"
            f"Current broken code:\n```\n{old_code}\n```\n\n"
        )

        # v4.1: Add conversation history so the patcher understands context
        if conversation_context:
            prompt += f"CONVERSATION HISTORY (what the user has been asking for):\n{conversation_context}\n\n"

        if error_ctx:
            prompt += f"Error diagnosis:\n{error_ctx}\n\n"

        prompt += f"Review feedback: {review_feedback}\n\n"

        if other_files_ctx:
            prompt += f"Other project files (for reference):\n{other_files_ctx}\n\n"
        prompt += f"Return the COMPLETE fixed version of {filename}. No markdown."

        new_code = invoke_llm(llm, [
            SystemMessage(content=_SYS_PATCHER),
            HumanMessage(content=prompt),
        ])

        new_code = _strip_fences(new_code)

        if new_code.strip():
            result[filename] = new_code
            print(f"  [Implementer] Patched: {filename} ({len(new_code)} chars)")
        else:
            print(f"  [Implementer] Patch empty for {filename}, keeping original")

    return result


# ─────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────

def _strip_fences(code: str) -> str:
    """Strip markdown code fences from LLM output."""
    code = code.strip()
    # Remove opening fence (```python, ```html, ```c, etc.)
    if code.startswith("```"):
        first_newline = code.find("\n")
        if first_newline != -1:
            code = code[first_newline + 1:]
    # Remove closing fence
    if code.rstrip().endswith("```"):
        code = code.rstrip()[:-3].rstrip()
    return code

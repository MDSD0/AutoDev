"""
quality_reviewer.py — AutoDev v4 Reviewer Agent.

v4 changes:
- Output-category-aware reviewing (file_output, gui_output, etc.)
- Increased code summary to 1500 chars per file
- Multimodal guard (only send images to Gemini)
- Generated-files-aware review (fixes image program validation)
"""
from __future__ import annotations

import os
import base64

from langchain_core.messages import HumanMessage, SystemMessage

from .llm_utils import invoke_llm_structured
from .config import Config
from .contracts import ReviewRubric, OutputCategory
from .context_builder import build_reviewer_context


# ─────────────────────────────────────────────────────────────
# Review Schema (matches ReviewRubric)
# ─────────────────────────────────────────────────────────────

REVIEW_REQUIRED_KEYS = ["spec_match", "file_completeness", "runtime_correctness",
                         "dependency_correctness", "output_quality", "verdict", "feedback"]

REVIEW_SCHEMA_DESC = """\
{
  "spec_match": <1-10>,
  "spec_match_evidence": "Quoted observation or line reference",
  "file_completeness": <1-10>,
  "file_completeness_evidence": "Which files are present/missing",
  "runtime_correctness": <1-10>,
  "runtime_correctness_evidence": "Exit code, output excerpt, or error",
  "dependency_correctness": <1-10>,
  "dependency_correctness_evidence": "Import errors or install log",
  "output_quality": <1-10>,
  "output_quality_evidence": "Output excerpt or structural observation",
  "visual_aesthetics": <1-10 or null>,
  "visual_aesthetics_evidence": "Description of the UI screenshot",
  "verdict": "PASS or RETRY",
  "feedback": "Concise summary of the most important issue to fix",
  "issues": ["Issue 1", "Issue 2"],
  "files_to_fix": ["filename.py"]
}"""


def _build_sys_reviewer(output_category: str) -> str:
    """Build system prompt with output-category-aware scoring rules."""
    category_rules = ""
    if output_category == OutputCategory.FILE_OUTPUT.value:
        category_rules = """
  - FILE OUTPUT PROGRAM: This program generates files (images, CSVs, etc.)
    as its primary output. Do NOT penalize for empty stdout — the output
    is the generated files. Check exec report for "Generated Files" or
    "Expected Files Found" entries.
    - output_quality: Score based on whether the expected files were created
    - runtime_correctness: Score 8+ if program completed and created files,
      even if stdout was empty"""
    elif output_category == OutputCategory.GUI_OUTPUT.value:
        category_rules = """
  - GUI PROGRAM: This program opens a GUI window.
    - runtime_correctness: Score 8+ if program started without errors
      (timeout = success for GUI programs, means the window is open)
    - output_quality: Score based on code quality, not stdout"""
    elif output_category == OutputCategory.SERVER_OUTPUT.value:
        category_rules = """
  - SERVER PROGRAM: This program runs a web server/Streamlit.
    - runtime_correctness: Score 8+ if server started without errors
      (timeout = success for servers, means it's running)
    - output_quality: Score based on code quality and features"""
    elif output_category == OutputCategory.BROWSER_OUTPUT.value:
        category_rules = """
  - HTML/BROWSER PROGRAM: Static web content.
    - runtime_correctness: Score based on HTML validation results
    - visual_aesthetics: Score the visual quality of the screenshot (must be >7)"""

    return f"""\
<role>Reviewer Agent — Critic</role>
<mission>Evaluate generated code against the spec using a structured evidence-based rubric.</mission>

<context_rules>
  - You receive: spec, plan, execution report, and code summary.
  - You are a CRITIC. You do NOT generate or suggest code.
  - Be specific, cite evidence (line numbers, output excerpts, file names).
</context_rules>

<scoring_dimensions>
  1. spec_match (1-10): Does the code accomplish what was asked?
     Does output match acceptance_criteria in spec?
  2. file_completeness (1-10): Are all plan.file_order files present and non-empty?
  3. runtime_correctness (1-10): Did execution succeed without errors/tracebacks?
  4. dependency_correctness (1-10): All imports available? No ModuleNotFoundError?
  5. output_quality (1-10): Is output useful and production-quality?
     (No placeholders, no TODOs, no "hello world" for complex tasks)
  6. visual_aesthetics (1-10 or null): If screenshot provided.
</scoring_dimensions>
{category_rules}

<verdict_rules>
  PASS: ALL dimensions >= {Config.MIN_REVIEW_SCORE}
  RETRY: ANY dimension < {Config.MIN_REVIEW_SCORE}
  (Average alone does NOT determine pass — every dimension must pass)
</verdict_rules>

<retry_rules>
  If RETRY, you MUST:
  - State the ONE most important issue in feedback
  - List specific files in files_to_fix
  - Provide evidence for every score < 7
</retry_rules>

<output>Return ONLY valid JSON. No markdown, no explanation outside JSON.</output>
<schema>{REVIEW_SCHEMA_DESC}</schema>"""


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def review_quality(
    spec: dict,
    plan: dict,
    files: dict[str, str],
    exec_report: dict,
    llm,
    retry_history: list[dict] | None = None,
    output_category: str = "cli_output",
) -> tuple[dict, dict]:
    """
    Review the quality of generated code against the spec.
    v4: output-category-aware, file-output-aware, multimodal-guarded.
    """
    # Build code summary (v4: 1500 chars per file, up from 400)
    files_summary = _build_code_summary(files)

    # Add generated file info to exec report summary
    generated_files = exec_report.get("generated_output_files", [])
    if generated_files:
        file_names = [os.path.basename(f) for f in generated_files]
        files_summary += f"\n\n[GENERATED OUTPUT FILES]: {', '.join(file_names)}"

    prompt = build_reviewer_context(spec, plan, exec_report, files_summary)
    
    # Add output category context
    prompt += f"\n\nOutput Category: {output_category}"
    if output_category == OutputCategory.FILE_OUTPUT.value:
        prompt += "\n[IMPORTANT] This is a file-generating program. Success = files were created. Empty stdout is expected."
        if generated_files:
            prompt += f"\nGenerated files: {[os.path.basename(f) for f in generated_files]}"

    # Prepend retry context if any
    if retry_history:
        retry_parts = [
            f"  Attempt #{r.get('attempt')}: {r.get('error_type', '?')} — {r.get('root_cause', r.get('feedback', '?'))}"
            for r in retry_history[-3:]
        ]
        prompt += "\n\nPrevious retry history:\n" + "\n".join(retry_parts)
    
    # Build message content
    sys_prompt = _build_sys_reviewer(output_category)
    content: list = [{"type": "text", "text": prompt}]
    
    # v4: Multimodal guard — only send images to Gemini
    visual_preview = exec_report.get("visual_preview", "")
    provider = getattr(llm, '_provider', '') if hasattr(llm, '_provider') else ''
    is_multimodal = 'gemini' in str(type(llm)).lower() or 'google' in str(type(llm)).lower()

    if visual_preview and os.path.exists(visual_preview) and is_multimodal:
        try:
            with open(visual_preview, "rb") as bf:
                img_b64 = base64.b64encode(bf.read()).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"}
            })
            content[0]["text"] += "\n\n[Visual Evaluate] I am appending a screenshot of the UI. Score visual_aesthetics comprehensively."
        except Exception as e:
            print(f"  [ReviewerAgent] Failed to load visual preview: {e}")

    messages = [
        SystemMessage(content=sys_prompt),
        HumanMessage(content=content),
    ]

    rubric_contract, error = invoke_llm_structured(llm, messages, ReviewRubric)

    if rubric_contract is None:
        print(f"  [ReviewerAgent] Structured output failed, using fallback: {error}")
        success = exec_report.get("success", False)
        error_summary = exec_report.get("error_summary", "")
        rubric_contract = ReviewRubric.failure_fallback(success, error_summary, output_category)

    review_dict = rubric_contract.to_dict()

    print(f"  [ReviewerAgent] verdict={review_dict['verdict']}, avg={rubric_contract.average_score:.1f}")

    return review_dict, review_dict


def _build_code_summary(files: dict[str, str]) -> str:
    """Build a reviewer-friendly code summary (v4: 1500 chars per file, up from 400)."""
    parts = []
    for name, code in files.items():
        preview = code[:1500] + ("... [TRUNCATED]" if len(code) > 1500 else "")
        parts.append(f"--- {name} ({len(code)} chars) ---\n{preview}")
    return "\n\n".join(parts) if parts else "(no files generated)"

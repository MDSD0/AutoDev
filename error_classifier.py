"""
error_classifier.py — AutoDev v4 root cause analysis.

v4 changes:
- Added "compilation" error type
- Added detection for "no output but files generated" case
- Added detection for compilation errors (C/Java/Go)
- Improved deterministic patterns for more error categories
"""
from __future__ import annotations

import re
import json
from typing import Optional

from langchain_core.messages import HumanMessage, SystemMessage

from .contracts import ErrorClassification
from .llm_utils import invoke_llm, validate_llm_json


# ─────────────────────────────────────────────────────────────
# Deterministic pattern rules
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# v4.2: Environment error patterns (cannot be fixed by code)
# ─────────────────────────────────────────────────────────────

_ENVIRONMENT_ERROR_PATTERNS = [
    # Network / DNS
    "nameresolutionerror",
    "failed to resolve",
    "name or service not known",
    "nodename nor servname provided",
    "temporary failure in name resolution",
    "connectionrefusederror",
    "connection refused",
    "max retries exceeded",
    "urlopen error",
    "httpsconnectionpool",
    "httpconnectionpool",
    "newconnectionerror",
    "sslerror",
    "certificate verify failed",
    "connectionreseterror",
    "remotedisconnected",
    "brokenpipeerror",
    "network is unreachable",
    # System / display
    "no display name",
    "cannot open display",
    "xdg_runtime_dir not set",
    # Permission / OS-level
    "operation not permitted",
    "read-only file system",
]

# v4.2: Harmless stderr warnings that should NOT trigger failures
_HARMLESS_STDERR_PATTERNS = [
    "requestsdependencywarning",
    "deprecationwarning",
    "futurewarning",
    "insecurerequestwarning",
    "resourcewarning",
    "userwarning",
    "syntaxwarning",
    "runtimewarning",
    "urllib3 v2 only supports",
    "doesn't match a supported version",
    "pygame_hide_support_prompt",
    "[autodev]",
]


def _is_harmless_stderr(stderr: str) -> bool:
    """Check if stderr contains ONLY harmless warnings (no real errors)."""
    if not stderr.strip():
        return True
    stderr_lower = stderr.lower()
    # Split into lines and check each
    for line in stderr.strip().split("\n"):
        line_stripped = line.strip().lower()
        if not line_stripped:
            continue
        # Skip lines that are just file paths or context
        if line_stripped.startswith(("/", "  ", "warnings.")):
            continue
        # Check if this line matches a harmless pattern
        if any(pat in line_stripped for pat in _HARMLESS_STDERR_PATTERNS):
            continue
        # This line is NOT harmless
        return False
    return True


def _is_environment_error(text: str) -> bool:
    """Check if the error text indicates an environment issue (not code bug)."""
    text_lower = text.lower()
    return any(pat in text_lower for pat in _ENVIRONMENT_ERROR_PATTERNS)


def _program_produced_output(exec_report: dict) -> bool:
    """Check if the program actually produced meaningful output despite errors."""
    output = exec_report.get("output", "")
    stdout = exec_report.get("stdout", "")
    generated_files = exec_report.get("generated_output_files", [])
    expected_found = exec_report.get("expected_files_found", [])
    
    has_generated_files = bool(generated_files)
    has_expected_files = bool(expected_found)
    # Check both 'output' and 'stdout' — executor uses 'output', some paths use 'stdout'
    combined_text = (output or "") + (stdout or "")
    has_text_output = bool(combined_text.strip()) and combined_text.strip() != "(no output)"
    has_meaningful_output = (
        has_generated_files or has_expected_files or
        (has_text_output and len(combined_text.strip()) > 20)
    )
    return has_meaningful_output


def _classify_deterministic(exec_report: dict, files: dict) -> Optional[ErrorClassification]:
    """
    Fast deterministic classification from stderr patterns.
    Returns None if the error is ambiguous and needs LLM analysis.
    
    v4.2: Added environment error detection and graceful degradation.
    """
    stderr = exec_report.get("stderr", "")
    stdout = exec_report.get("stdout", "")
    output = exec_report.get("output", "")
    error_type_raw = exec_report.get("error_type", "")
    error_summary = exec_report.get("error_summary", "")
    success = exec_report.get("success", False)
    exit_code = exec_report.get("exit_code", -1)

    if success:
        return None  # No error to classify

    stderr_lower = stderr.lower()
    combined_text = f"{stderr} {output} {error_summary}".lower()

    # ── v4.2: Environment errors (network, DNS, permissions) ──
    # These CANNOT be fixed by rewriting code.
    if _is_environment_error(combined_text):
        # But check: did the program handle it gracefully?
        if _program_produced_output(exec_report) and exit_code == 0:
            # Program caught the error itself and produced fallback output
            print("  [ErrorClassifier] Environment error with graceful degradation — accepting")
            return ErrorClassification(
                error_type="runtime",
                root_cause="Environment error (network/DNS) but program handled it gracefully with fallback",
                affected_files=[],
                severity="low",
                suggested_strategy="escalate",  # Don't retry — escalate to accept
            )
        else:
            # Program crashed due to environment issue
            print("  [ErrorClassifier] Environment error (not fixable by code changes)")
            return ErrorClassification(
                error_type="runtime",
                root_cause=f"Environment error (network/DNS/permission issue, not a code bug): {error_summary[:200]}",
                affected_files=[],
                severity="low",
                suggested_strategy="escalate",  # Can't fix by code — tell user
            )

    # ── v4.2: Harmless stderr only (warnings, not errors) ──
    # If exit_code is 0 and stderr is just warnings, it's not a failure.
    if exit_code == 0 and _is_harmless_stderr(stderr):
        print("  [ErrorClassifier] Exit 0 + harmless stderr — not a real error")
        return None  # Not actually an error

    # ── Compilation errors ───────────────────────────────
    if error_type_raw == "compilation":
        return ErrorClassification(
            error_type="compilation",
            root_cause=f"Compilation failed: {error_summary or stderr.strip().split(chr(10))[-1][:200]}",
            affected_files=_extract_affected_files(stderr, files),
            severity="high",
            suggested_strategy="syntax_fix",
        )

    # ── Syntax errors ────────────────────────────────────
    if error_type_raw == "syntax" or "syntaxerror" in stderr_lower or "indentationerror" in stderr_lower:
        affected = _extract_affected_files(stderr, files)
        return ErrorClassification(
            error_type="syntax",
            root_cause=f"Syntax error: {error_summary or stderr.strip().split(chr(10))[-1][:200]}",
            affected_files=affected,
            severity="high",
            suggested_strategy="syntax_fix",
        )

    # ── Import / module not found ────────────────────────
    if error_type_raw == "import" or "modulenotfounderror" in stderr_lower or "no module named" in stderr_lower:
        m = re.search(r"no module named ['\"]([^'\"]+)['\"]", stderr_lower)
        missing_mod = m.group(1) if m else "unknown"
        return ErrorClassification(
            error_type="import",
            root_cause=f"Missing module: {missing_mod}",
            affected_files=_extract_affected_files(stderr, files),
            severity="medium",
            suggested_strategy="dependency_fix",
        )

    # ── Timeout ──────────────────────────────────────────
    if error_type_raw == "timeout" or "timed out" in stderr_lower or "timeout" in stderr_lower:
        return ErrorClassification(
            error_type="timeout",
            root_cause="Execution timed out — possible infinite loop or blocking call",
            affected_files=_extract_affected_files(stderr, files),
            severity="high",
            suggested_strategy="logic_fix",
        )

    # ── File/entrypoint not found ────────────────────────
    if error_type_raw == "file_type_mismatch" or "entrypoint" in error_summary.lower():
        return ErrorClassification(
            error_type="file_mismatch",
            root_cause=f"Entrypoint file missing or misnamed: {error_summary}",
            affected_files=[],
            severity="critical",
            suggested_strategy="architecture_fix",
        )

    # ── Dependency / package errors ──────────────────────
    if error_type_raw == "dependency" or "pip" in stderr_lower or "no matching distribution" in stderr_lower:
        return ErrorClassification(
            error_type="dependency",
            root_cause=f"Dependency error: {error_summary}",
            affected_files=[],
            severity="medium",
            suggested_strategy="dependency_fix",
        )

    # ── Common runtime errors — deterministic but non-architectural ──
    runtime_patterns = [
        ("typeerror", "logic"),
        ("valueerror", "logic"),
        ("keyerror", "logic"),
        ("attributeerror", "logic"),
        ("nameerror", "logic"),
        ("zerodivisionerror", "logic"),
        ("filenotfounderror", "logic"),
        ("permissionerror", "logic"),
        ("oserror", "logic"),
        ("recursionerror", "logic"),
        ("memoryerror", "logic"),
    ]
    for pat, etype in runtime_patterns:
        if pat in stderr_lower:
            return ErrorClassification(
                error_type="runtime",
                root_cause=f"{pat.capitalize()}: {error_summary or stderr.strip().split(chr(10))[-1][:200]}",
                affected_files=_extract_affected_files(stderr, files),
                severity="medium",
                suggested_strategy="logic_fix",
            )

    # ── C/C++ specific errors ────────────────────────────
    if any(pat in stderr_lower for pat in ["undefined reference", "undeclared identifier", "linker error"]):
        return ErrorClassification(
            error_type="compilation",
            root_cause=f"Linker/compile error: {error_summary or stderr.strip().split(chr(10))[-1][:200]}",
            affected_files=_extract_affected_files(stderr, files),
            severity="high",
            suggested_strategy="syntax_fix",
        )

    # ── Java specific errors ─────────────────────────────
    if "error:" in stderr_lower and (".java" in stderr or "javac" in stderr_lower):
        return ErrorClassification(
            error_type="compilation",
            root_cause=f"Java compilation error: {error_summary}",
            affected_files=_extract_affected_files(stderr, files),
            severity="high",
            suggested_strategy="syntax_fix",
        )

    # Ambiguous — needs LLM
    return None


def _extract_affected_files(stderr: str, files: dict) -> list[str]:
    """Extract file names mentioned in tracebacks or compiler output."""
    affected = []
    project_files = set(files.keys())

    # Python traceback: File "..." line N
    for match in re.finditer(r'File "([^"]+)"', stderr):
        path = match.group(1)
        basename = path.split("/")[-1].split("\\")[-1]
        if basename in project_files:
            if basename not in affected:
                affected.append(basename)

    # C/C++/Java: filename.c:N:M: error:
    for match in re.finditer(r'([\w./]+\.\w+):(\d+):(?:\d+:)?\s*error', stderr):
        path = match.group(1)
        basename = path.split("/")[-1].split("\\")[-1]
        if basename in project_files:
            if basename not in affected:
                affected.append(basename)

    # If nothing found, return primary files as guess
    if not affected:
        primary_exts = {".py", ".c", ".cpp", ".java", ".go", ".rs", ".js", ".ts"}
        candidates = [f for f in project_files if any(f.endswith(ext) for ext in primary_exts)]
        return candidates[:3]

    return affected


# ─────────────────────────────────────────────────────────────
# LLM classification for ambiguous cases
# ─────────────────────────────────────────────────────────────

_CLASSIFIER_SCHEMA = """\
{
  "error_type": "syntax|import|runtime|logic|architecture|timeout|dependency|file_mismatch|compilation|unknown",
  "root_cause": "Concise one-sentence root cause explanation",
  "affected_files": ["filename.py"],
  "severity": "low|medium|high|critical",
  "suggested_strategy": "syntax_fix|dependency_fix|logic_fix|architecture_fix|full_regen|escalate"
}"""

_SYS_CLASSIFIER = """\
<role>Error Classifier</role>
<mission>Analyse a code execution failure and identify the root cause precisely.</mission>
<rules>
  - error_type: choose the most specific type from the enum
  - root_cause: one clear sentence explaining WHY it failed (not just WHAT failed)
  - affected_files: list ONLY files that need to be changed to fix this
  - severity: low=cosmetic, medium=fixable, high=systemic, critical=requires replan
  - suggested_strategy:
      syntax_fix → fix syntax in affected files
      dependency_fix → install or fix package names
      logic_fix → fix logic/algorithm in affected files
      architecture_fix → the plan itself is wrong, needs replanning
      full_regen → regenerate all files from scratch
      escalate → the error is an ENVIRONMENT issue (network, DNS, permissions)
                 or the program already handles the error gracefully.
                 Cannot be fixed by rewriting code. Accept as-is.

  CRITICAL RULES:
  - If the error is a NETWORK/DNS/CONNECTION issue (e.g. NameResolutionError,
    ConnectionRefused, MaxRetriesExceeded, SSLError), set strategy to 'escalate'.
    These are ENVIRONMENT problems, not code bugs.
  - If the program CAUGHT the error itself (try/except) and still produced
    meaningful output (files, stdout), the code is CORRECT. Set strategy to 'escalate'.
  - If stderr contains only WARNINGS (DeprecationWarning, RequestsDependencyWarning),
    not actual errors, set strategy to 'escalate' — these are not bugs.
  - Only suggest 'logic_fix' if there is an actual bug IN THE CODE.
  - Be concise. Do not explain beyond what is needed.
</rules>
<output>Return ONLY valid JSON matching the schema. No markdown, no explanation.</output>"""


def _classify_with_llm(
    exec_report: dict,
    files: dict,
    spec: dict,
    review_rubric: dict,
    llm,
) -> ErrorClassification:
    """Use LLM to classify ambiguous errors."""
    stderr = exec_report.get("stderr", "")
    output = exec_report.get("output", "")
    error_summary = exec_report.get("error_summary", "")
    exec_method = exec_report.get("execution_method", "")

    # Build concise file summary (names + first 300 chars each)
    file_summary = "\n".join(
        f"--- {name} ---\n{code[:300]}{'...' if len(code) > 300 else ''}"
        for name, code in list(files.items())[:5]
    )

    # Pull failing scores from rubric as clues
    rubric_clues = ""
    if review_rubric:
        failing = [
            f"{k}={v}" for k, v in {
                "spec_match": review_rubric.get("spec_match"),
                "runtime_correctness": review_rubric.get("runtime_correctness"),
                "dependency_correctness": review_rubric.get("dependency_correctness"),
            }.items() if v is not None and int(v) < 7
        ]
        if failing:
            rubric_clues = f"\nFailing review dimensions: {', '.join(failing)}"

    prompt = (
        f"EXECUTION REPORT:\n"
        f"  success={exec_report.get('success', False)}\n"
        f"  exit_code={exec_report.get('exit_code', -1)}\n"
        f"  method={exec_method}\n"
        f"  error_summary={error_summary}\n"
        f"\nSTDERR:\n{stderr[:1500]}\n"
        f"\nOUTPUT:\n{output[:500]}\n"
        f"\nPROJECT FILES (first 300 chars each):\n{file_summary}"
        f"{rubric_clues}\n\n"
        f"PROJECT TYPE: {spec.get('output_type', 'unknown')}\n"
        f"\nSchema:\n{_CLASSIFIER_SCHEMA}"
    )

    raw = invoke_llm(llm, [
        SystemMessage(content=_SYS_CLASSIFIER),
        HumanMessage(content=prompt),
    ])

    parsed, error = validate_llm_json(
        raw,
        required_keys=["error_type", "root_cause", "suggested_strategy"],
        llm=llm,
        schema_description=_CLASSIFIER_SCHEMA,
        max_repair_attempts=1,
    )

    if parsed is None:
        return ErrorClassification.unknown(f"LLM classification failed: {error}")

    try:
        return ErrorClassification.from_dict(parsed)
    except Exception as e:
        return ErrorClassification.unknown(f"ErrorClassification parse failed: {e}")


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def classify_error(
    exec_report: dict,
    files: dict,
    spec: dict,
    review_rubric: dict | None = None,
    llm=None,
) -> ErrorClassification:
    """
    Classify an execution or review failure.

    Strategy:
    1. Check for environment errors first (network, DNS — unfixable by code)
    2. Check for graceful degradation (program handled error + produced output)
    3. Try deterministic rules (fast, reliable)
    4. If ambiguous, use LLM for deeper analysis
    5. Fallback to unknown if all fail
    """
    review_rubric = review_rubric or {}
    exit_code = exec_report.get("exit_code", -1)

    # ── v4.2: If execution succeeded but only has harmless stderr warnings ──
    # This catches cases where the executor marked it as failure due to warnings
    # in stderr but the actual exit code and output are fine.
    if exit_code == 0 and _program_produced_output(exec_report):
        stderr = exec_report.get("stderr", "")
        if _is_harmless_stderr(stderr) or _is_environment_error(f"{stderr} {exec_report.get('output', '')}"):
            print("  [ErrorClassifier] Exit 0 + produced output + harmless/env stderr — treating as success")
            return ErrorClassification(
                error_type="runtime",
                root_cause="Program completed successfully with warnings or handled environment errors gracefully",
                affected_files=[],
                severity="low",
                suggested_strategy="escalate",
            )

    # If execution succeeded but review failed, it's a logic/spec issue
    if exec_report.get("success", False):
        # Look for failing rubric dimensions
        spec_match = review_rubric.get("spec_match", 10)
        output_quality = review_rubric.get("output_quality", 10)

        if int(spec_match) < 5:
            return ErrorClassification(
                error_type="logic",
                root_cause=f"Code runs but doesn't match spec: {review_rubric.get('spec_match_evidence', '')}",
                affected_files=review_rubric.get("files_to_fix", []),
                severity="high",
                suggested_strategy="logic_fix",
            )
        if int(output_quality) < 5:
            return ErrorClassification(
                error_type="logic",
                root_cause=f"Code runs but output quality is poor: {review_rubric.get('output_quality_evidence', '')}",
                affected_files=review_rubric.get("files_to_fix", []),
                severity="medium",
                suggested_strategy="logic_fix",
            )
        # Generic spec mismatch
        return ErrorClassification(
            error_type="logic",
            root_cause=f"Code runs but fails quality review: {review_rubric.get('feedback', '')}",
            affected_files=review_rubric.get("files_to_fix", []),
            severity="medium",
            suggested_strategy="logic_fix",
        )

    # Try deterministic first
    result = _classify_deterministic(exec_report, files)
    if result is not None:
        print(f"  [ErrorClassifier] Deterministic: {result.error_type}")
        return result

    # Fall back to LLM
    if llm is not None:
        print(f"  [ErrorClassifier] Using LLM for ambiguous error...")
        result = _classify_with_llm(exec_report, files, spec, review_rubric, llm)
        print(f"  [ErrorClassifier] LLM: {result.error_type}")
        return result

    # Final fallback
    return ErrorClassification.unknown("No LLM available and deterministic rules did not match")

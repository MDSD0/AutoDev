"""
context_builder.py — AutoDev v4 Context Scoping

v4 changes:
- Increased context budgets to match new config values
- Added conversation context builder for follow-up messages
- Added execution result context for reviewer (includes generated files)
"""
from __future__ import annotations

import json
from .config import Config

def truncate_context(text: str, max_chars: int) -> str:
    """Truncates context safely and appends a truncation warning if needed."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [TRUNCATED DUE TO CONTEXT LIMIT]"

def build_spec_context(task: str) -> str:
    """Spec Agent: Input is raw user task only."""
    return truncate_context(task, Config.CTX_SPEC_MAX_CHARS)

def build_planner_context(spec: dict) -> str:
    """Planner Agent: Input is SpecContract only."""
    spec_str = json.dumps(spec, indent=2)
    return truncate_context(spec_str, Config.CTX_PLANNER_MAX_CHARS)

def build_implementer_context(spec: dict, plan: dict, error: dict | None = None, affected_files_content: dict | None = None) -> str:
    """
    Implementer Agent: Input is SpecContract + PlanContract + relevant file contents + concise error summary.
    Does NOT pass the full history.
    """
    # Spec and Plan
    spec_brief = json.dumps({
        "output_type": spec.get("output_type"),
        "entrypoint": spec.get("entrypoint"),
        "acceptance_criteria": spec.get("acceptance_criteria", []),
        "output_category": spec.get("output_category", "cli_output"),
        "expected_output_files": spec.get("expected_output_files", []),
    }, indent=2)
    
    plan_brief = json.dumps({
        "file_order": plan.get("file_order"),
        "project_structure": plan.get("project_structure", {}),
        "runtime": plan.get("runtime"),
        "execution_command": plan.get("execution_command", ""),
    }, indent=2)

    ctx = f"Project Spec (brief):\n{spec_brief}\n\nBuild Plan (brief):\n{plan_brief}\n\n"

    # Files Context
    if affected_files_content:
        ctx += "AFFECTED FILES:\n"
        for name, content in affected_files_content.items():
            ctx += f"--- {name} ---\n{content}\n\n"
    
    # Error Summary Context
    if error:
        ctx += f"FAILURE DIAGNOSIS:\nError Type: {error.get('error_type')}\n"
        ctx += f"Root Cause: {error.get('root_cause')}\n"
        ctx += f"Strategy: {error.get('suggested_strategy')}\n"
    
    return truncate_context(ctx, Config.CTX_IMPL_MAX_CHARS)

def build_reviewer_context(spec: dict, plan: dict, exec_report: dict, files_summary: str) -> str:
    """
    Reviewer Agent: Input is SpecContract + PlanContract + Execution Report + code diffs/summary.
    """
    # Expose only critical criteria
    spec_brief = json.dumps({
        "output_type": spec.get("output_type"),
        "acceptance_criteria": spec.get("acceptance_criteria", []),
        "entrypoint": spec.get("entrypoint"),
        "output_category": spec.get("output_category", "cli_output"),
        "expected_output_files": spec.get("expected_output_files", []),
    }, indent=2)

    plan_brief = json.dumps({
        "file_order": plan.get("file_order", []),
        "entrypoint": plan.get("entrypoint", ""),
        "execution_command": plan.get("execution_command", ""),
    }, indent=2)

    # v4: Include generated file info in exec summary
    generated_files = exec_report.get("generated_output_files", [])
    gen_info = ""
    if generated_files:
        import os
        gen_names = [os.path.basename(f) for f in generated_files]
        gen_info = f"\nGenerated output files: {gen_names}"

    exec_summary = (
        f"success={exec_report.get('success', False)}\n"
        f"exit_code={exec_report.get('exit_code', -1)}\n"
        f"error_type={exec_report.get('error_type', 'unknown')}\n"
        f"error_summary={exec_report.get('error_summary', '')}\n"
        f"execution_method={exec_report.get('execution_method', '')}{gen_info}\n"
        f"stdout/stderr:\n{exec_report.get('output', '(no output)')[:2000]}"
    )

    ctx = (
        f"SPEC (brief):\n{spec_brief}\n\n"
        f"PLAN (brief):\n{plan_brief}\n\n"
        f"CODE SUMMARY:\n{files_summary}\n\n"
        f"EXECUTION REPORT:\n{exec_summary}"
    )
    
    return truncate_context(ctx, Config.CTX_REVIEWER_MAX_CHARS)


def build_conversation_context(
    task: str,
    spec: dict | None,
    files: dict | None,
    history_summary: str = "",
) -> str:
    """v4: Build context for conversation follow-up messages."""
    parts = [f"User's new request:\n{task}"]

    if spec:
        parts.append(f"\nPrevious spec:\n{json.dumps(spec, indent=2)[:2000]}")

    if files:
        file_list = ", ".join(files.keys())
        parts.append(f"\nExisting files: {file_list}")

    if history_summary:
        parts.append(f"\nConversation context:\n{history_summary}")

    return truncate_context("\n".join(parts), Config.CTX_IMPL_MAX_CHARS)

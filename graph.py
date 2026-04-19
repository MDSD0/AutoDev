"""
graph.py — AutoDev v4 LangGraph pipeline.

v4 COMPLETE REWRITE:
  - Two modes: New Project pipeline vs. Conversation follow-up
  - Conversation router classifies intent (modify/fix/explain/new/execute)
  - Output-category-aware execution (fixes image/file programs)
  - Per-session RAG with code/error/turn storage
  - Single RAG factory (no more duplicate singletons)
  - Wired retry_controller (was dead code in v3)
  - Human-in-the-loop at any convergence point

Architecture: Orchestrator hub-and-spoke.
  New Project: refiner → spec → rag → plan → deps → implement → execute → review
  Follow-up:   conversation_router → (modify|fix|explain|execute)
"""
from __future__ import annotations

import json
import os
import platform
from typing import TypedDict, Literal, Any, Annotated

from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, SystemMessage

from .config import Config
from .llm_utils import get_llm, invoke_llm
from .contracts import SpecContract, PlanContract, ErrorClassification, ReviewRubric, OutputCategory
from .spec_extractor import extract_spec
from .planner import create_plan
from .file_generator import generate_files
from .dependency_manager import install_dependencies
from .executor import execute_project, launch_file
from .session_memory import (
    get_rag, save_state, save_history, save_files,
    get_workspace_dir, load_state, load_files, load_history,
)


# ─────────────────────────────────────────────────────────────
# V4 State
# ─────────────────────────────────────────────────────────────

class V4State(TypedDict):
    # ── Identity ─────────────────────────────────────────────
    session_id: str

    # ── Task input ───────────────────────────────────────────
    task: str
    refined_prompt: str

    # ── IMMUTABLE CONTRACTS ───────────────────────────────────
    spec: dict              # Serialized SpecContract
    spec_frozen: bool       # True once spec is set
    plan: dict              # Serialized PlanContract
    plan_frozen: bool       # True once plan is set

    # ── Code files ────────────────────────────────────────────
    files: dict             # {filepath: content}
    entrypoint: str
    output_type: str

    # ── Dependencies ──────────────────────────────────────────
    dependencies: list
    dep_install_log: str
    dep_success: bool

    # ── Execution ─────────────────────────────────────────────
    exec_report: dict
    exec_output: str
    exec_success: bool

    # ── Error Classification ──────────────────────────────────
    error_classification: dict  # Serialized ErrorClassification

    # ── Review ────────────────────────────────────────────────
    review_rubric: dict         # Serialized ReviewRubric
    review_verdict: str
    review_feedback: str
    quality_scores: dict

    # ── Orchestrator control ──────────────────────────────────
    current_phase: str          # Which phase the orchestrator is in
    next_action: str            # Decision made by orchestrator node

    # ── Retry control ─────────────────────────────────────────
    iterations: int
    max_iterations: int
    retry_history: list
    retry_strategy: str
    status: str                 # running|success|failed|awaiting_user
    user_guidance: str

    # ── Convergence detection ─────────────────────────────────
    consecutive_same_errors: int

    # ── Memory ────────────────────────────────────────────────
    rag_context: str
    history: list

    # ── Outputs ───────────────────────────────────────────────
    saved_files: list
    workspace_dir: str

    # ── Config ────────────────────────────────────────────────
    provider: str
    refine_prompt: bool
    model_used: str

    # ── v4 NEW: Conversation mode ─────────────────────────────
    conversation_mode: bool     # True for follow-up messages
    intent: str                 # classify: modify|fix|explain|new_project|execute
    output_category: str        # OutputCategory value for executor
    expected_output_files: list  # Files the program should create
    execution_command: str       # Explicit run command from plan
    generated_output_files: list  # Files created during execution
    mode: str                   # "plan" (full pipeline), "fast" (patch-only), or "auto" (smart routing)


# ─────────────────────────────────────────────────────────────
# History helper
# ─────────────────────────────────────────────────────────────

def _history_append(hist: list, role: str, name: str, content: str) -> list:
    h = list(hist)
    h.append({"role": role, "name": name, "content": content})
    return h


# ─────────────────────────────────────────────────────────────
# System Prompts
# ─────────────────────────────────────────────────────────────

_SYS_REFINER = (
    "You are a prompt engineering expert.\n"
    "Rewrite the user's request to be specific, unambiguous, and production-ready.\n"
    "Support any output type: HTML/CSS/JS web apps, Python scripts, Streamlit dashboards, "
    "PyQt6 GUI apps, C/C++ programs, Java apps, Go scripts, Rust programs, shell scripts, "
    "Node.js scripts, or any other programming language.\n"
    "Keep the same intent. 2-3 sentences. Return ONLY the rewritten prompt."
)

_SYS_CONVERSATION_ROUTER = """\
You are a conversation intent classifier for an autonomous coding system.

Given a user's follow-up message and the context of their session (previous task, current files, status), classify the intent.

INTENTS:
- "chat": User is making casual conversation, greeting, or asking a general question that does NOT require code changes (e.g. "sup", "hello", "how are you", "thanks", "what can you do?")
- "modify": User wants to change, add, or update features in THE SAME existing project
- "fix": User reports a bug or error they want fixed in THE SAME existing project
- "explain": User asks a question about the code (no changes needed)
- "new_project": User wants a COMPLETELY DIFFERENT project/application (topic doesn't match current project)
- "execute": User wants to re-run or run with different parameters

CRITICAL RULES:
1. If the message is a greeting, small talk, thanks, or any non-coding request → "chat"
2. If the user's request is about a DIFFERENT TOPIC than the current project → "new_project"
   Examples: Current project is "calculator" but user asks "make a snake game" → "new_project"
   Current project is "portfolio website" but user asks "write a bitcoin tracker" → "new_project"
3. If the user says "build X", "create X", "make X" and X is unrelated to current project → "new_project"
4. Only use "modify" if the request is clearly about changing the EXISTING project

Return ONLY one of: chat, modify, fix, explain, new_project, execute
No explanation, just the single word."""

_SYS_SMART_MODE_ROUTER = """\
You are an intelligent mode router for an autonomous coding system called AutoDev.

Given the user's message, decide which mode to use:

- "chat": The message is casual conversation, a greeting, a question about capabilities, 
  small talk, or anything that does NOT require writing/modifying code.
  Examples: "sup", "hello", "what can you do?", "thanks!", "how does this work?"
  
- "plan": The message describes a NEW project, application, or substantial feature that 
  requires planning, spec extraction, architecture design, and full code generation.
  Examples: "Build a calculator app", "Create a web scraper", "Make a todo app with database"
  
- "fast": The message describes a SMALL, specific code change, bug fix, or tweak to 
  existing code that doesn't need full re-planning.
  Examples: "change the button color to blue", "fix the login bug", "add a dark mode toggle"

IMPORTANT: Be conservative about triggering "plan" mode. Most casual messages should be "chat".
If in doubt between "chat" and "plan", choose "chat".

Return ONLY one of: chat, plan, fast
No explanation, just the single word."""


# ─────────────────────────────────────────────────────────────
# Graph Nodes
# ─────────────────────────────────────────────────────────────

def node_prompt_refiner(state: V4State) -> dict:
    sid = state.get("session_id", "?")
    print(f"\n[{sid}] ── REFINER ─────────────────────────────────")
    print(f"  Task: {str(state.get('task', ''))[:80]}")

    if not state.get("refine_prompt", True):
        print("  Skipped (toggle off).")
        return {"refined_prompt": state["task"], "current_phase": "refiner_done"}

    llm, prov = get_llm(str(state.get("provider", "auto")))
    resp = invoke_llm(llm, [
        SystemMessage(content=_SYS_REFINER),
        HumanMessage(content=str(state["task"])),
    ])
    print(f"  Refined: {resp[:100]}")
    hist = _history_append(list(state.get("history", [])), "assistant", "Prompt Refiner", resp)
    save_history(sid, hist)
    return {
        "refined_prompt": resp,
        "model_used": prov,
        "history": hist,
        "current_phase": "refiner_done",
    }


def node_spec_agent(state: V4State) -> dict:
    """Spec Agent: raw task → immutable SpecContract."""
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── SPEC AGENT ──────────────────────────────")

    if state.get("spec_frozen") and state.get("spec"):
        print("  Spec already frozen — skipping.")
        return {"current_phase": "spec_done"}

    llm, prov = get_llm(str(state.get("provider", "auto")))
    task = str(state.get("refined_prompt") or state.get("task", ""))

    raw_spec, error = extract_spec(task, llm, refined_prompt=task)

    if error:
        print(f"  Warning: {error}")

    try:
        contract = SpecContract.from_dict(raw_spec)
        spec_dict = contract.to_dict()
        frozen = True
        print(f"  SpecContract frozen: output_type={contract.output_type}, "
              f"category={contract.output_category}, "
              f"files={contract.expected_files}")
    except Exception as e:
        print(f"  SpecContract validation failed: {e}. Using raw dict.")
        spec_dict = raw_spec
        frozen = True

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Spec Agent",
        f"**Project Spec:**\n```json\n{json.dumps(spec_dict, indent=2)}\n```"
    )
    save_history(sid, hist)

    # Store spec in session RAG
    try:
        rag = get_rag()
        rag.add_memory(sid, f"Project spec: {json.dumps(spec_dict)}", {"type": "spec"})
    except Exception as e:
        print(f"  RAG store error: {e}")

    return {
        "spec": spec_dict,
        "spec_frozen": frozen,
        "output_type": spec_dict.get("output_type", "python"),
        "entrypoint": spec_dict.get("entrypoint", "main.py"),
        "dependencies": spec_dict.get("dependencies", []),
        "output_category": spec_dict.get("output_category", "cli_output"),
        "expected_output_files": spec_dict.get("expected_output_files", []),
        "model_used": prov,
        "history": hist,
        "current_phase": "spec_done",
    }


def node_rag_retriever(state: V4State) -> dict:
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── RAG ──────────────────────────────────────")
    try:
        ctx = get_rag().retrieve(
            sid,
            str(state.get("refined_prompt") or state.get("task", "")),
        )
        print(f"  Context: {len(ctx)} chars")
        return {"rag_context": ctx, "current_phase": "rag_done"}
    except Exception as e:
        print(f"  RAG skipped: {e}")
        return {"rag_context": "", "current_phase": "rag_done"}


def node_planner_agent(state: V4State) -> dict:
    """Planner Agent: SpecContract → immutable PlanContract."""
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── PLANNER AGENT ───────────────────────────")

    if state.get("plan_frozen") and state.get("plan") and state.get("current_phase") != "replan":
        print("  Plan already frozen — skipping.")
        return {"current_phase": "plan_done"}

    llm, prov = get_llm(str(state.get("provider", "auto")))
    spec = state.get("spec", {})
    rag = str(state.get("rag_context", ""))[:Config.CTX_PLANNER_MAX_CHARS // 2]

    raw_plan, error = create_plan(spec, llm, rag_context=rag)

    if error:
        print(f"  Warning: {error}")

    try:
        contract = PlanContract.from_dict(raw_plan)
        plan_dict = contract.to_dict()
        frozen = True
        print(f"  PlanContract frozen: files={contract.file_order}, "
              f"runtime={contract.runtime}")
    except Exception as e:
        print(f"  PlanContract validation failed: {e}. Using raw dict.")
        plan_dict = raw_plan
        frozen = True

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Planner Agent",
        f"**Build Plan:**\n```json\n{json.dumps(plan_dict, indent=2)}\n```"
    )
    save_history(sid, hist)

    deps = list(set(
        state.get("dependencies", []) + plan_dict.get("packages", [])
    ))

    return {
        "plan": plan_dict,
        "plan_frozen": frozen,
        "dependencies": deps,
        "entrypoint": plan_dict.get("entrypoint", state.get("entrypoint", "main.py")),
        "execution_command": plan_dict.get("execution_command", ""),
        "model_used": prov,
        "history": hist,
        "current_phase": "plan_done",
    }


def node_dependency_installer(state: V4State) -> dict:
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── DEPENDENCY INSTALLER ────────────────────")
    workspace = get_workspace_dir(sid)
    deps = state.get("dependencies", [])
    files = state.get("files", {})
    output_type = state.get("output_type", "python")

    # Skip for languages that don't use pip
    skip_types = {"html", "shell", "c", "cpp", "java", "go", "rust", "swift", "kotlin"}
    if output_type in skip_types:
        print(f"  Skipped ({output_type} project).")
        return {
            "dep_success": True,
            "dep_install_log": f"No pip dependencies needed ({output_type} project).",
            "workspace_dir": workspace,
            "current_phase": "deps_done",
        }

    success, log = install_dependencies(deps, workspace, files if files else None)

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Dependency Installer",
        f"**Dependencies:** {'Installed' if success else 'Failed'}\n{log[:500]}"
    )
    save_history(sid, hist)

    print(f"  success={success}")
    return {
        "dep_success": success,
        "dep_install_log": log,
        "workspace_dir": workspace,
        "history": hist,
        "current_phase": "deps_done",
    }


def node_implementer_agent(state: V4State) -> dict:
    """Implementer Agent: generates or patches files.
    
    v4 FIX: In conversation mode or retries, ALWAYS patches existing files
    instead of regenerating from scratch. The user's follow-up message is
    treated as review_feedback so the patcher can see it.
    
    v4.1 FIX: Now passes conversation history to the patcher so it has
    full context of previous user requests (like ChatGPT/Claude would).
    """
    sid = state.get("session_id", "?")
    iters = int(state.get("iterations", 0))
    conversation_mode = state.get("conversation_mode", False)
    mode = state.get("mode", "plan")
    print(f"[{sid}] ── IMPLEMENTER AGENT (iter {iters + 1}, conv={conversation_mode}, mode={mode}) ───")
    llm, prov = get_llm(str(state.get("provider", "auto")))

    spec = state.get("spec", {})
    plan = state.get("plan", {})
    retry_history = state.get("retry_history", [])
    current_files = state.get("files", {})
    review_feedback = state.get("review_feedback", "")
    user_guidance = state.get("user_guidance", "")
    error_classification = state.get("error_classification", {})
    task = state.get("task", "")

    # ── Build conversation context for the patcher ──
    # This gives the LLM full conversational memory like ChatGPT/Claude.
    conversation_context = ""
    if conversation_mode:
        history = state.get("history", [])
        # Include last N turns of conversation for context
        recent_turns = history[-12:]  # ~6 user/assistant pairs
        conv_parts = []
        for turn in recent_turns:
            role = turn.get("role", "")
            name = turn.get("name", "")
            content = turn.get("content", "")
            # Skip implementer/executor/classifier noise — only keep meaningful context
            if name in ("User", "Code Assistant", "Prompt Refiner", "Reviewer Agent", "Mission Control"):
                preview = content[:500]
                conv_parts.append(f"[{name}]: {preview}")
        if conv_parts:
            conversation_context = "\n".join(conv_parts)

    # ── Build feedback context ──
    # In conversation mode, the user's new message IS the feedback
    if conversation_mode and task:
        review_feedback = f"USER REQUEST: {task}\n\n{review_feedback}"
    if user_guidance:
        review_feedback = f"USER GUIDANCE: {user_guidance}\n\n{review_feedback}"

    # ── Decide: patch or full regen ──
    should_patch = False
    force_full_regen = False

    # In conversation mode with existing files: ALWAYS patch
    if conversation_mode and current_files:
        should_patch = True
        print("  [Implementer] Conversation mode — patching existing files")

    # In retry with existing files: patch (unless same error 2x)
    elif iters > 0 and current_files:
        should_patch = True
        # Check for same error 2x — force full regen
        if len(retry_history) >= 2:
            last_two = retry_history[-2:]
            if (last_two[0].get("error_type") == last_two[1].get("error_type") and
                last_two[0].get("root_cause", "")[:50] == last_two[1].get("root_cause", "")[:50]):
                force_full_regen = True
                should_patch = False
                print("  [Implementer] Same error 2x — forcing full regeneration")

    # Determine which files are affected
    affected_files: list[str] = []
    if should_patch and not force_full_regen:
        # Try error classification first
        affected_files = error_classification.get("affected_files", [])
        if not affected_files:
            rubric = state.get("review_rubric", {})
            affected_files = rubric.get("files_to_fix", [])
        # In conversation mode without specific error info, affect all files
        # (the patcher will use the user's request to decide what to change)
        if not affected_files and conversation_mode:
            affected_files = list(current_files.keys())
            print(f"  [Implementer] No specific affected files — patching all: {affected_files}")

    if should_patch and affected_files and not force_full_regen:
        # PATCH mode
        print(f"  [Implementer] Targeted patch: {affected_files}")
        files = generate_files(
            spec=spec,
            plan=plan,
            llm=llm,
            retry_history=retry_history if retry_history else None,
            current_files=current_files,
            review_feedback=review_feedback,
            affected_files=affected_files,
            conversation_context=conversation_context,
        )
    else:
        # FULL GENERATION
        print(f"  [Implementer] Full generation")
        files = generate_files(
            spec=spec,
            plan=plan,
            llm=llm,
            retry_history=None,
            current_files=None,
            review_feedback=review_feedback if (iters > 0 or conversation_mode) else "",
            affected_files=None,
            conversation_context=conversation_context if conversation_mode else "",
        )

    # Save files to workspace
    workspace = get_workspace_dir(sid)
    save_files(sid, files)

    # Store code in RAG for future retrieval
    try:
        get_rag().store_code_snapshot(sid, files)
    except Exception as e:
        print(f"  RAG code store error: {e}")

    action = "Patched" if (should_patch and not force_full_regen) else "Generated"
    file_list = ", ".join(f"{name} ({len(code)} chars)" for name, code in files.items())
    hist = _history_append(
        list(state.get("history", [])), "assistant", "Implementer Agent",
        f"**{action} {len(files)} file(s) (iter {iters + 1}):** {file_list}"
    )
    save_history(sid, hist)

    return {
        "files": files,
        "model_used": prov,
        "history": hist,
        "workspace_dir": workspace,
        "current_phase": "implement_done",
        "error_classification": {},
    }


def node_executor(state: V4State) -> dict:
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── EXECUTOR ────────────────────────────────")
    llm, prov = get_llm(str(state.get("provider", "auto")))
    files = state.get("files", {})
    entrypoint = state.get("entrypoint", "main.py")
    output_type = state.get("output_type", "python")
    output_category = state.get("output_category", "cli_output")
    execution_command = state.get("execution_command", "")
    expected_output_files = state.get("expected_output_files", [])
    workspace = get_workspace_dir(sid)

    report = execute_project(
        files, entrypoint, output_type, workspace,
        llm=llm,
        output_category=output_category,
        execution_command=execution_command,
        expected_output_files=expected_output_files,
    )

    output = report.get("output", "(no output)")
    success = report.get("success", False)
    generated_files = report.get("generated_output_files", [])
    print(f"  success={success}, error_type={report.get('error_type', '?')}")
    print(f"  output: {output[:100].replace(chr(10), ' ')}")
    if generated_files:
        print(f"  generated_files: {[os.path.basename(f) for f in generated_files]}")

    hist = _history_append(
        list(state.get("history", [])), "user", "Executor",
        f"**Execution {'(PASS)' if success else '(FAIL)'}** "
        f"(type={output_type}, category={output_category}, method={report.get('execution_method', '?')})\n"
        f"```\n{output[:2000]}\n```"
    )
    save_history(sid, hist)

    # Propagate corrected output_category if the executor overrode it
    effective_category = report.get("effective_output_category", output_category)

    return {
        "exec_report": report,
        "exec_output": output,
        "exec_success": success,
        "generated_output_files": generated_files,
        "output_category": effective_category,
        "history": hist,
        "current_phase": "exec_done",
    }


def node_error_classifier(state: V4State) -> dict:
    """Error Classifier: deterministic + LLM root cause analysis before any retry.
    
    v4 FIX: This node now increments `iterations` and appends to `retry_history`.
    Previously only the reviewer did this, causing infinite loops when the
    executor-fail → classifier → implementer path skipped the reviewer entirely.
    """
    from .error_classifier import classify_error

    sid = state.get("session_id", "?")
    iters = int(state.get("iterations", 0))
    new_iters = iters + 1
    print(f"[{sid}] ── ERROR CLASSIFIER (iter {new_iters}) ────────────────────────")

    exec_report = state.get("exec_report", {})
    files = state.get("files", {})
    spec = state.get("spec", {})
    review_rubric = state.get("review_rubric", {})
    output_category = state.get("output_category", "cli_output")
    llm, _ = get_llm(str(state.get("provider", "auto")))

    # ── v4 FIX: Server/GUI timeout is SUCCESS, not an error ──
    # If a Streamlit/Flask/GUI program timed out, the executor should have
    # marked it as success. But if somehow it didn't (e.g. the plan_command
    # path had wrong timeout_is_success), catch it here.
    error_type_raw = exec_report.get("error_type", "")
    if error_type_raw == "timeout" and output_category in (
        OutputCategory.SERVER_OUTPUT.value,
        OutputCategory.GUI_OUTPUT.value,
    ):
        print(f"  [Classifier] Timeout for {output_category} program = SUCCESS (expected behavior)")
        hist = _history_append(
            list(state.get("history", [])), "assistant", "Error Classifier",
            f"**Timeout is expected** for {output_category} programs — treating as success."
        )
        save_history(sid, hist)
        # Override to success and route to reviewer
        return {
            "exec_success": True,
            "exec_report": {**exec_report, "success": True, "error_type": "none", "error_summary": ""},
            "error_classification": {},
            "history": hist,
            "current_phase": "exec_done",  # Re-route through orchestrator → reviewer
        }

    classification = classify_error(
        exec_report=exec_report,
        files=files,
        spec=spec,
        review_rubric=review_rubric,
        llm=llm,
    )

    print(f"  error_type={classification.error_type}, "
          f"severity={classification.severity}, "
          f"strategy={classification.suggested_strategy}, "
          f"affected={classification.affected_files}")

    # ── v4.2: ESCALATE = accept as success ──────────────────────
    # When the classifier says "escalate", it means:
    # 1. The error is an environment issue (network, DNS, permissions) — can't fix by code
    # 2. The program handled the error gracefully and still produced output
    # 3. Stderr only has harmless warnings, not real errors
    # In ALL these cases: stop retrying and accept the result.
    if classification.suggested_strategy == "escalate":
        print(f"  [Classifier] ESCALATE: {classification.root_cause}")
        print(f"  [Classifier] Accepting result — not a code bug, no retry needed")
        hist = _history_append(
            list(state.get("history", [])), "assistant", "Error Classifier",
            f"**Accepted (iter {new_iters}):** {classification.root_cause}\n"
            f"This is not a code bug — the program handled errors gracefully or "
            f"this is an environment issue. Accepting current output."
        )
        save_history(sid, hist)
        # Override to success and route to reviewer for final scoring
        return {
            "exec_success": True,
            "exec_report": {
                **exec_report,
                "success": True,
                "error_type": "none",
                "error_summary": f"[Accepted] {classification.root_cause}",
            },
            "error_classification": classification.to_dict(),
            "history": hist,
            "current_phase": "exec_done",  # Route to reviewer, not implementer
        }

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Error Classifier",
        f"**Root Cause (iter {new_iters}):** {classification.error_type} — {classification.root_cause}\n"
        f"Strategy: {classification.suggested_strategy}\n"
        f"Affected files: {classification.affected_files}"
    )
    save_history(sid, hist)

    # Store error pattern in RAG for learning
    try:
        get_rag().store_error_pattern(
            sid, classification.error_type,
            classification.root_cause,
            classification.suggested_strategy,
        )
    except Exception as e:
        print(f"  RAG error store failed: {e}")

    # ── v4 FIX: Update retry_history here (not just in reviewer) ──
    retry_history = list(state.get("retry_history", []))
    retry_history.append({
        "attempt": new_iters,
        "error_type": classification.error_type,
        "root_cause": classification.root_cause,
        "fix_applied": classification.suggested_strategy,
        "feedback": exec_report.get("error_summary", ""),
        "strategy": classification.suggested_strategy,
    })

    # Track consecutive same errors for convergence detection
    prev_errors = [r.get("error_type") for r in retry_history[-Config.CONVERGENCE_SAME_ERROR_THRESHOLD:]]
    consecutive = 0
    if len(prev_errors) >= Config.CONVERGENCE_SAME_ERROR_THRESHOLD and all(
        e == classification.error_type for e in prev_errors
    ):
        consecutive = state.get("consecutive_same_errors", 0) + 1
    else:
        consecutive = 0

    result = {
        "error_classification": classification.to_dict(),
        "consecutive_same_errors": consecutive,
        "iterations": new_iters,
        "retry_history": retry_history,
        "history": hist,
        "current_phase": "error_classified",
    }

    # Save state so we can track progress
    full_state = dict(state)
    full_state.update(result)
    save_state(sid, full_state)

    return result


def node_reviewer_agent(state: V4State) -> dict:
    """Reviewer Agent: structured rubric scoring."""
    sid = state.get("session_id", "?")
    iters = int(state.get("iterations", 0))
    print(f"[{sid}] ── REVIEWER AGENT (iter {iters + 1}) ─────────────")
    llm, prov = get_llm(str(state.get("provider", "auto")))

    spec = state.get("spec", {})
    plan = state.get("plan", {})
    files = state.get("files", {})
    exec_report = state.get("exec_report", {})
    retry_history = state.get("retry_history", [])
    output_category = state.get("output_category", "cli_output")

    from .quality_reviewer import review_quality
    rubric_dict, raw_review = review_quality(
        spec, plan, files, exec_report, llm, retry_history,
        output_category=output_category,
    )

    try:
        rubric = ReviewRubric.from_dict(rubric_dict)
    except Exception as e:
        print(f"  ReviewRubric parse failed: {e}. Using fallback.")
        rubric = ReviewRubric.failure_fallback(
            exec_success=exec_report.get("success", False),
            error_summary=exec_report.get("error_summary", ""),
            output_category=output_category,
        )

    verdict = rubric.verdict
    avg = rubric.average_score
    new_iters = iters + 1

    if verdict == "PASS":
        status = "success"
    else:
        status = "running"

    # Convergence check
    consecutive = state.get("consecutive_same_errors", 0)
    if (consecutive >= Config.CONVERGENCE_SAME_ERROR_THRESHOLD and
            new_iters >= state.get("max_iterations", Config.MAX_RETRIES)):
        status = "awaiting_user"

    # Update retry history
    new_retry_history = list(retry_history)
    ec = state.get("error_classification", {})
    new_retry_history.append({
        "attempt": new_iters,
        "error_type": ec.get("error_type", exec_report.get("error_type", "unknown")),
        "root_cause": ec.get("root_cause", ""),
        "fix_applied": ec.get("suggested_strategy", ""),
        "scores": rubric.scores_dict,
        "feedback": rubric.feedback,
        "strategy": ec.get("suggested_strategy", "logic_fix"),
    })

    saved = []
    if status == "success":
        workspace = get_workspace_dir(sid)
        for name in files:
            saved.append(os.path.join(workspace, name))
        try:
            get_rag().add_memory(
                sid,
                f"Successful solution for: {state.get('task', '')[:200]}\n"
                f"Files: {list(files.keys())}",
                {"type": "solution"},
            )
        except Exception as e:
            print(f"  RAG store error: {e}")

    verdict_emoji = "(PASS)" if verdict == "PASS" else "(RETRY)"
    score_str = ", ".join(f"{k}={v}" for k, v in rubric.scores_dict.items())
    hist_content = (
        f"**Review {verdict_emoji}** — Verdict: {verdict} (avg: {avg}/10)\n"
        f"Scores: {score_str}\n"
        f"Feedback: {rubric.feedback}"
    )
    if status == "awaiting_user":
        hist_content += "\n\n[WARNING] Convergence detected — awaiting user guidance."

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Reviewer Agent",
        hist_content
    )
    save_history(sid, hist)

    result = {
        "review_rubric": rubric.to_dict(),
        "review_verdict": verdict,
        "review_feedback": rubric.feedback,
        "quality_scores": rubric.scores_dict,
        "retry_strategy": ec.get("suggested_strategy", "logic_fix"),
        "iterations": new_iters,
        "retry_history": new_retry_history,
        "status": status,
        "saved_files": saved,
        "model_used": prov,
        "history": hist,
        "current_phase": "review_done",
    }

    full_state = dict(state)
    full_state.update(result)
    save_state(sid, full_state)

    print(f"  verdict={verdict}, status={status}, avg={avg}, "
          f"consecutive_errors={consecutive}")
    return result


def node_human_gate(state: V4State) -> dict:
    """Human escalation gate — surfaces diagnosis and waits for guidance."""
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── HUMAN GATE ──────────────────────────────")

    retry_history = state.get("retry_history", [])
    ec = state.get("error_classification", {})
    rubric = state.get("review_rubric", {})

    parts = ["All automatic retries exhausted. Here is what happened:\n"]
    for r in retry_history[-5:]:
        parts.append(
            f"  Attempt #{r.get('attempt', '?')}: "
            f"[{r.get('strategy', r.get('fix_applied', '?'))}] "
            f"{r.get('root_cause', r.get('feedback', 'No diagnosis'))}"
        )
    if rubric:
        scores = rubric.get("scores", {}) or {
            k: rubric.get(k) for k in [
                "spec_match", "file_completeness", "runtime_correctness",
                "dependency_correctness", "output_quality"
            ] if k in rubric
        }
        parts.append(f"\nLatest scores: {scores}")
    parts.append(
        "\nYou can provide additional guidance to help the system fix the issue."
    )

    diagnosis = "\n".join(parts)
    hist = _history_append(
        list(state.get("history", [])), "assistant", "Mission Control",
        f"**Human Input Required**\n{diagnosis}"
    )
    save_history(sid, hist)

    result = {
        "status": "awaiting_user",
        "human_gate_reason": diagnosis,
        "history": hist,
        "current_phase": "awaiting_user",
    }
    full_state = dict(state)
    full_state.update(result)
    save_state(sid, full_state)
    return result


def node_conversation_router(state: V4State) -> dict:
    """v4: Route follow-up messages based on intent classification."""
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── CONVERSATION ROUTER ─────────────────────")

    task = state.get("task", "")
    llm, prov = get_llm(str(state.get("provider", "auto")))

    # Build context for intent classification
    prev_task = state.get("spec", {}).get("problem_statement", "")
    file_names = list(state.get("files", {}).keys())
    status = state.get("status", "")

    context = (
        f"Previous task: {prev_task}\n"
        f"Current files: {file_names}\n"
        f"Status: {status}\n"
        f"User's new message: {task}"
    )

    try:
        intent = invoke_llm(llm, [
            SystemMessage(content=_SYS_CONVERSATION_ROUTER),
            HumanMessage(content=context),
        ]).strip().lower()

        # Validate intent
        valid_intents = {"chat", "modify", "fix", "explain", "new_project", "execute"}
        if intent not in valid_intents:
            intent = "modify"  # Default to modify
    except Exception as e:
        print(f"  Intent classification failed: {e}")
        intent = "modify"

    print(f"  Intent: {intent}")

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Conversation Router",
        f"Intent classified: **{intent}**"
    )
    save_history(sid, hist)

    result = {
        "intent": intent,
        "history": hist,
        "current_phase": f"intent_{intent}",
        "model_used": prov,
    }

    # ── v4.3 FIX: new_project intent = COMPLETE state reset ──
    # When the user asks for a completely different project in the same session,
    # we must clear ALL old project state. Previously we only cleared spec/plan/files
    # but left output_type, entrypoint, dependencies, output_category, refined_prompt,
    # execution_command, and rag_context — all of which leaked into the new generation.
    if intent == "new_project":
        print(f"  [Router] NEW PROJECT in existing session — FULL state reset")
        workspace = get_workspace_dir(sid)
        # Clean old output files from workspace
        import shutil
        if os.path.isdir(workspace):
            for item in os.listdir(workspace):
                item_path = os.path.join(workspace, item)
                # Keep state.json and history.json, remove everything else
                if item in ("state.json", "history.json"):
                    continue
                try:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                except Exception as e:
                    print(f"  [Router] Could not remove {item}: {e}")

        # Wipe RAG context from old project so it doesn't pollute new spec/plan
        try:
            get_rag().wipe_session(sid)
            print(f"  [Router] Wiped old session RAG")
        except Exception as e:
            print(f"  [Router] RAG wipe error: {e}")

        # Store the new task in fresh RAG
        try:
            get_rag().add_memory(sid, f"New project request: {task}", {"type": "task"})
        except Exception:
            pass

        result.update({
            # ── Contract & Plan ──
            "spec": {},
            "spec_frozen": False,
            "plan": {},
            "plan_frozen": False,
            # ── Code & Execution ──
            "files": {},
            "entrypoint": "main.py",
            "output_type": "",
            "output_category": "cli_output",
            "execution_command": "",
            "expected_output_files": [],
            "generated_output_files": [],
            # ── Dependencies ──
            "dependencies": [],
            "dep_install_log": "",
            "dep_success": False,
            # ── Pipeline control ──
            "conversation_mode": False,  # Treat as fresh project
            "refined_prompt": "",
            "rag_context": "",
            "iterations": 0,
            "retry_history": [],
            "retry_strategy": "",
            # ── Execution state ──
            "exec_report": {},
            "exec_output": "",
            "exec_success": False,
            "error_classification": {},
            "review_rubric": {},
            "review_verdict": "",
            "review_feedback": "",
            "quality_scores": {},
            "consecutive_same_errors": 0,
            "user_guidance": "",
            "saved_files": [],
        })

    return result


def node_explain_agent(state: V4State) -> dict:
    """v4: Answer questions about existing code without modifying it."""
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── EXPLAIN AGENT ───────────────────────────")

    llm, prov = get_llm(str(state.get("provider", "auto")))
    task = state.get("task", "")
    files = state.get("files", {})

    # Build code context
    code_ctx = "\n\n".join(
        f"--- {name} ---\n{code[:2000]}"
        for name, code in list(files.items())[:5]
    )

    response = invoke_llm(llm, [
        SystemMessage(content="You are a helpful coding assistant. Answer the user's question about their code. Be specific, cite file names and line numbers. Do NOT generate new code unless asked."),
        HumanMessage(content=f"Code:\n{code_ctx}\n\nQuestion: {task}"),
    ])

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Code Assistant",
        response
    )
    save_history(sid, hist)

    result = {
        "history": hist,
        "status": "success",
        "current_phase": "explain_done",
    }
    full_state = dict(state)
    full_state.update(result)
    save_state(sid, full_state)
    return result


def node_chat_agent(state: V4State) -> dict:
    """v4.1: Handle casual conversation without triggering any code pipeline.
    
    Responds to greetings, small talk, and general questions with a friendly
    personality. Does NOT modify code, run pipelines, or change state.
    """
    sid = state.get("session_id", "?")
    print(f"[{sid}] ── CHAT AGENT ─────────────────────────────")

    llm, prov = get_llm(str(state.get("provider", "auto")))
    task = state.get("task", "")
    files = state.get("files", {})

    # Build minimal context
    file_list = list(files.keys()) if files else []
    context = ""
    if file_list:
        context = f"\n\nContext: The user has an active project with these files: {file_list}"

    response = invoke_llm(llm, [
        SystemMessage(content=(
            "You are AutoDev, an autonomous AI coding assistant. "
            "The user is having a casual conversation with you. "
            "Be friendly, helpful, and concise. "
            "If they greet you, greet them back warmly and ask how you can help with coding. "
            "If they ask what you can do, explain your capabilities: building apps, fixing bugs, "
            "generating code in any language, debugging, and explaining code. "
            "Do NOT generate code or start any project unless explicitly asked. "
            "Keep responses short and natural (1-3 sentences)."
        )),
        HumanMessage(content=f"{task}{context}"),
    ])

    print(f"  Response: {response[:100]}")

    hist = _history_append(
        list(state.get("history", [])), "assistant", "AutoDev",
        response
    )
    save_history(sid, hist)

    result = {
        "history": hist,
        "status": "success",
        "current_phase": "chat_done",
        "model_used": prov,
    }
    full_state = dict(state)
    full_state.update(result)
    save_state(sid, full_state)
    return result


def node_smart_mode_router(state: V4State) -> dict:
    """v4.1: Smart mode router for first messages in auto mode.
    
    Classifies the user's first message to determine if it needs:
    - chat: Just a friendly response (no code)
    - plan: Full pipeline (spec → plan → deps → code → execute → review)
    - fast: Quick patch (code → execute only)
    
    This prevents casual messages like 'sup' from triggering the full
    planning pipeline.
    """
    sid = state.get("session_id", "?")
    task = state.get("task", "")
    print(f"[{sid}] ── SMART MODE ROUTER ────────────────────────")
    print(f"  Message: {task[:80]}")

    llm, prov = get_llm(str(state.get("provider", "auto")))

    try:
        decision = invoke_llm(llm, [
            SystemMessage(content=_SYS_SMART_MODE_ROUTER),
            HumanMessage(content=task),
        ]).strip().lower()

        valid = {"chat", "plan", "fast"}
        if decision not in valid:
            decision = "plan"  # Default to plan for safety
    except Exception as e:
        print(f"  Smart routing failed: {e}")
        decision = "plan"

    print(f"  Decision: {decision}")

    hist = _history_append(
        list(state.get("history", [])), "assistant", "Smart Router",
        f"Mode: **{decision}** — {'Chatting' if decision == 'chat' else 'Full Planning' if decision == 'plan' else 'Quick Patch'}"
    )
    save_history(sid, hist)

    return {
        "intent": decision,
        "mode": decision if decision != "chat" else "auto",
        "history": hist,
        "current_phase": f"smart_{decision}",
        "model_used": prov,
    }


# ─────────────────────────────────────────────────────────────
# Orchestrator Routing
# ─────────────────────────────────────────────────────────────

def route_after_spec(state: V4State) -> str:
    return "rag_retriever"


def route_orchestrator(state: V4State) -> str:
    """Central routing hub."""
    phase = state.get("current_phase", "")
    status = state.get("status", "running")
    iters = int(state.get("iterations", 0))
    max_iters = int(state.get("max_iterations", Config.MAX_RETRIES))

    # Terminal states
    if status in ("success", "failed", "awaiting_user"):
        return "end"

    if phase == "rag_done":
        return "planner_agent"

    if phase == "plan_done":
        return "dependency_installer"

    if phase == "deps_done":
        return "implementer_agent"

    if phase == "implement_done":
        return "executor"

    if phase == "exec_done":
        if state.get("exec_success", False):
            return "reviewer_agent"
        else:
            return "error_classifier"

    if phase == "error_classified":
        ec = state.get("error_classification", {})
        strategy = ec.get("suggested_strategy", "logic_fix")
        consecutive = state.get("consecutive_same_errors", 0)

        if iters >= max_iters:
            return "human_gate"

        if consecutive >= Config.CONVERGENCE_SAME_ERROR_THRESHOLD:
            return "human_gate"

        if strategy == "architecture_fix":
            return "planner_agent"

        if strategy == "dependency_fix":
            return "dependency_installer"

        return "implementer_agent"

    if phase == "review_done":
        verdict = state.get("review_verdict", "RETRY")

        if verdict == "PASS":
            return "end"

        if iters >= max_iters:
            return "human_gate"

        consecutive = state.get("consecutive_same_errors", 0)
        if consecutive >= Config.CONVERGENCE_SAME_ERROR_THRESHOLD:
            return "human_gate"

        return "error_classifier"

    # Default fallback
    print(f"  [Orchestrator] Unknown phase '{phase}' — defaulting to implementer")
    return "implementer_agent"


def route_conversation(state: V4State) -> str:
    """Route based on conversation intent."""
    intent = state.get("intent", "modify")

    if intent == "chat":
        return "chat_agent"

    if intent == "new_project":
        return "prompt_refiner"

    if intent == "explain":
        return "explain_agent"

    if intent == "execute":
        return "executor"

    # modify and fix both go through implementer → executor → review
    return "implementer_agent"


def route_smart_mode(state: V4State) -> str:
    """Route first message in auto mode based on smart classification."""
    intent = state.get("intent", "plan")

    if intent == "chat":
        return "chat_agent"
    
    if intent == "fast":
        return "implementer_agent"

    # plan → full pipeline
    return "prompt_refiner"


# ─────────────────────────────────────────────────────────────
# Graph Builder
# ─────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(V4State)

    # Register nodes (main pipeline only — no conversation_router or explain_agent)
    g.add_node("prompt_refiner", node_prompt_refiner)
    g.add_node("spec_agent", node_spec_agent)
    g.add_node("rag_retriever", node_rag_retriever)
    g.add_node("planner_agent", node_planner_agent)
    g.add_node("dependency_installer", node_dependency_installer)
    g.add_node("implementer_agent", node_implementer_agent)
    g.add_node("executor", node_executor)
    g.add_node("error_classifier", node_error_classifier)
    g.add_node("reviewer_agent", node_reviewer_agent)
    g.add_node("human_gate", node_human_gate)

    # Entry chain: refiner → spec → rag → plan → deps → implement → execute
    g.set_entry_point("prompt_refiner")
    g.add_edge("prompt_refiner", "spec_agent")
    g.add_edge("spec_agent", "rag_retriever")
    g.add_edge("rag_retriever", "planner_agent")
    g.add_edge("planner_agent", "dependency_installer")
    g.add_edge("dependency_installer", "implementer_agent")
    g.add_edge("implementer_agent", "executor")

    # After executor: route via orchestrator
    g.add_conditional_edges("executor", route_orchestrator, {
        "error_classifier": "error_classifier",
        "reviewer_agent": "reviewer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    # After error classifier: orchestrator decides fix strategy
    g.add_conditional_edges("error_classifier", route_orchestrator, {
        "planner_agent": "planner_agent",
        "dependency_installer": "dependency_installer",
        "implementer_agent": "implementer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    # After reviewer: orchestrator decides pass/retry/escalate
    g.add_conditional_edges("reviewer_agent", route_orchestrator, {
        "error_classifier": "error_classifier",
        "human_gate": "human_gate",
        "end": END,
    })

    # Terminal nodes
    g.add_edge("human_gate", END)

    return g.compile()


def build_conversation_graph():
    """v4: Build graph for conversation mode (follow-up messages)."""
    g = StateGraph(V4State)

    g.add_node("conversation_router", node_conversation_router)
    g.add_node("prompt_refiner", node_prompt_refiner)
    g.add_node("spec_agent", node_spec_agent)
    g.add_node("rag_retriever", node_rag_retriever)
    g.add_node("planner_agent", node_planner_agent)
    g.add_node("dependency_installer", node_dependency_installer)
    g.add_node("implementer_agent", node_implementer_agent)
    g.add_node("executor", node_executor)
    g.add_node("error_classifier", node_error_classifier)
    g.add_node("reviewer_agent", node_reviewer_agent)
    g.add_node("human_gate", node_human_gate)
    g.add_node("explain_agent", node_explain_agent)
    g.add_node("chat_agent", node_chat_agent)

    g.set_entry_point("conversation_router")

    # Route based on intent
    g.add_conditional_edges("conversation_router", route_conversation, {
        "chat_agent": "chat_agent",
        "prompt_refiner": "prompt_refiner",
        "explain_agent": "explain_agent",
        "executor": "executor",
        "implementer_agent": "implementer_agent",
    })

    # New project path
    g.add_edge("prompt_refiner", "spec_agent")
    g.add_edge("spec_agent", "rag_retriever")
    g.add_edge("rag_retriever", "planner_agent")
    g.add_edge("planner_agent", "dependency_installer")
    g.add_edge("dependency_installer", "implementer_agent")
    g.add_edge("implementer_agent", "executor")

    # After executor
    g.add_conditional_edges("executor", route_orchestrator, {
        "error_classifier": "error_classifier",
        "reviewer_agent": "reviewer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_conditional_edges("error_classifier", route_orchestrator, {
        "planner_agent": "planner_agent",
        "dependency_installer": "dependency_installer",
        "implementer_agent": "implementer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_conditional_edges("reviewer_agent", route_orchestrator, {
        "error_classifier": "error_classifier",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_edge("human_gate", END)
    g.add_edge("explain_agent", END)
    g.add_edge("chat_agent", END)

    return g.compile()


def build_auto_graph():
    """v4.1: Build graph for auto mode on first messages.
    
    Starts with smart_mode_router which classifies the message as:
    - chat → chat_agent (just reply, no code)
    - plan → full pipeline (spec → plan → deps → code → execute → review)
    - fast → quick patch (code → execute only)
    """
    g = StateGraph(V4State)

    g.add_node("smart_mode_router", node_smart_mode_router)
    g.add_node("chat_agent", node_chat_agent)
    g.add_node("prompt_refiner", node_prompt_refiner)
    g.add_node("spec_agent", node_spec_agent)
    g.add_node("rag_retriever", node_rag_retriever)
    g.add_node("planner_agent", node_planner_agent)
    g.add_node("dependency_installer", node_dependency_installer)
    g.add_node("implementer_agent", node_implementer_agent)
    g.add_node("executor", node_executor)
    g.add_node("error_classifier", node_error_classifier)
    g.add_node("reviewer_agent", node_reviewer_agent)
    g.add_node("human_gate", node_human_gate)

    g.set_entry_point("smart_mode_router")

    # Route based on smart classification
    g.add_conditional_edges("smart_mode_router", route_smart_mode, {
        "chat_agent": "chat_agent",
        "prompt_refiner": "prompt_refiner",
        "implementer_agent": "implementer_agent",
    })

    # Full pipeline path
    g.add_edge("prompt_refiner", "spec_agent")
    g.add_edge("spec_agent", "rag_retriever")
    g.add_edge("rag_retriever", "planner_agent")
    g.add_edge("planner_agent", "dependency_installer")
    g.add_edge("dependency_installer", "implementer_agent")
    g.add_edge("implementer_agent", "executor")

    # After executor
    g.add_conditional_edges("executor", route_orchestrator, {
        "error_classifier": "error_classifier",
        "reviewer_agent": "reviewer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_conditional_edges("error_classifier", route_orchestrator, {
        "planner_agent": "planner_agent",
        "dependency_installer": "dependency_installer",
        "implementer_agent": "implementer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_conditional_edges("reviewer_agent", route_orchestrator, {
        "error_classifier": "error_classifier",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_edge("human_gate", END)
    g.add_edge("chat_agent", END)

    return g.compile()


_graph = None
_conv_graph = None
_fast_graph = None
_auto_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph

def get_conversation_graph():
    global _conv_graph
    if _conv_graph is None:
        _conv_graph = build_conversation_graph()
    return _conv_graph

def get_auto_graph():
    global _auto_graph
    if _auto_graph is None:
        _auto_graph = build_auto_graph()
    return _auto_graph


def build_fast_graph():
    """v4.1: Minimal fast-patch pipeline — implementer → executor only.
    
    Skips: spec, plan, deps, review, conversation router.
    Used for quick patches when the user knows exactly what they want.
    """
    g = StateGraph(V4State)

    g.add_node("implementer_agent", node_implementer_agent)
    g.add_node("executor", node_executor)
    g.add_node("error_classifier", node_error_classifier)
    g.add_node("reviewer_agent", node_reviewer_agent)
    g.add_node("human_gate", node_human_gate)

    g.set_entry_point("implementer_agent")
    g.add_edge("implementer_agent", "executor")

    # After executor: check success/fail
    g.add_conditional_edges("executor", route_orchestrator, {
        "error_classifier": "error_classifier",
        "reviewer_agent": "reviewer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    # After error classifier: back to implementer for another patch
    g.add_conditional_edges("error_classifier", _route_fast_error, {
        "implementer_agent": "implementer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    # After reviewer: pass or retry
    g.add_conditional_edges("reviewer_agent", _route_fast_review, {
        "implementer_agent": "implementer_agent",
        "human_gate": "human_gate",
        "end": END,
    })

    g.add_edge("human_gate", END)

    return g.compile()


def _route_fast_error(state: V4State) -> str:
    """Fast-mode error routing: always go back to implementer (no planner/deps)."""
    iters = int(state.get("iterations", 0))
    max_iters = int(state.get("max_iterations", Config.MAX_RETRIES))
    consecutive = state.get("consecutive_same_errors", 0)

    if iters >= max_iters:
        return "human_gate"
    if consecutive >= Config.CONVERGENCE_SAME_ERROR_THRESHOLD:
        return "human_gate"
    return "implementer_agent"


def _route_fast_review(state: V4State) -> str:
    """Fast-mode review routing: pass → end, fail → retry implementer."""
    status = state.get("status", "running")
    verdict = state.get("review_verdict", "RETRY")
    iters = int(state.get("iterations", 0))
    max_iters = int(state.get("max_iterations", Config.MAX_RETRIES))

    if verdict == "PASS" or status in ("success", "failed"):
        return "end"
    if status == "awaiting_user":
        return "human_gate"
    if iters >= max_iters:
        return "human_gate"
    return "implementer_agent"


def get_fast_graph():
    global _fast_graph
    if _fast_graph is None:
        _fast_graph = build_fast_graph()
    return _fast_graph


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def _make_init_state(
    task: str,
    session_id: str,
    provider: str = "auto",
    refine_prompt: bool = True,
    max_retries: int = 6,
) -> V4State:
    """Build initial state for a new task."""
    return V4State(
        session_id=session_id,
        task=task,
        refined_prompt="",
        spec={},
        spec_frozen=False,
        plan={},
        plan_frozen=False,
        files={},
        entrypoint="main.py",
        output_type="",
        dependencies=[],
        dep_install_log="",
        dep_success=False,
        exec_report={},
        exec_output="",
        exec_success=False,
        error_classification={},
        review_rubric={},
        review_verdict="",
        review_feedback="",
        quality_scores={},
        current_phase="init",
        next_action="",
        iterations=0,
        max_iterations=max_retries,
        retry_history=[],
        retry_strategy="",
        status="running",
        user_guidance="",
        consecutive_same_errors=0,
        rag_context="",
        history=[{"role": "user", "name": "User", "content": task}],
        saved_files=[],
        workspace_dir="",
        provider=provider,
        refine_prompt=refine_prompt,
        model_used="",
        # v4 fields
        conversation_mode=False,
        intent="",
        output_category="cli_output",
        expected_output_files=[],
        execution_command="",
        generated_output_files=[],
    )


def stream_task(
    task: str,
    session_id: str,
    provider: str = "auto",
    refine_prompt: bool = True,
    max_retries: int = 6,
    mode: str = "auto",
):
    """Stream a task.
    
    Modes:
      - "plan": Full pipeline (spec → plan → deps → code → execute → review)
      - "fast": Quick patch (code → execute only, skip spec/plan/deps/review)
      - "auto": Smart routing — uses conversation router to decide:
                 query → explain, patch → fast implementer, new project → full plan
    
    Auto-detects conversation mode for follow-up messages.
    """
    saved = load_state(session_id)

    if saved and saved.get("status") in ("success", "failed", "awaiting_user"):
        # ── Follow-up / Conversation Mode ──
        print(f"\n[{session_id}] ═══ CONVERSATION MODE (mode={mode}) ═══════════")

        # Restore state and add new message
        old_files = load_files(session_id)
        if old_files:
            saved["files"] = old_files

        saved["task"] = task
        saved["status"] = "running"
        saved["conversation_mode"] = True
        saved["mode"] = mode
        saved["provider"] = provider
        saved["user_guidance"] = ""
        saved["review_feedback"] = ""  # Clear old feedback
        saved["error_classification"] = {}  # Clear old errors
        saved["exec_success"] = False
        saved["iterations"] = 0
        saved["max_iterations"] = max_retries
        saved["consecutive_same_errors"] = 0
        saved["retry_history"] = []
        saved["refine_prompt"] = refine_prompt
        saved["current_phase"] = "conversation"

        hist = saved.get("history", [])
        hist = _history_append(hist, "user", "User", task)
        saved["history"] = hist

        # Store turn in RAG
        try:
            get_rag().store_conversation_turn(session_id, "user", "User", task, len(hist))
        except Exception:
            pass

        if mode == "fast":
            # Fast mode: skip router, go straight to implementer → executor
            print(f"  [Fast Mode] Skipping spec/plan/deps/review")
            graph = get_fast_graph()
        elif mode == "plan":
            # Plan mode: force full pipeline even for follow-ups
            # Reset spec/plan so they get regenerated from scratch
            print(f"  [Plan Mode] Full pipeline (re-spec, re-plan, re-implement)")
            saved["spec_frozen"] = False
            saved["plan_frozen"] = False
            saved["spec"] = {}
            saved["plan"] = {}
            saved["files"] = {}
            saved["entrypoint"] = "main.py"
            saved["output_type"] = ""
            saved["output_category"] = "cli_output"
            saved["execution_command"] = ""
            saved["expected_output_files"] = []
            saved["dependencies"] = []
            saved["refined_prompt"] = ""
            saved["rag_context"] = ""
            saved["conversation_mode"] = False  # Treat as new project
            graph = get_graph()
        else:
            # Auto mode: smart routing via conversation router
            # Router classifies intent → explain/modify/fix/new_project/execute
            graph = get_conversation_graph()

        for event in graph.stream(saved, config={"recursion_limit": 100}):
            for node_name, data in event.items():
                yield {"node": node_name, "data": data}
    else:
        # ── New Project Mode ──
        if mode == "fast" and saved and saved.get("files"):
            # Fast mode on existing session that hasn't completed yet
            print(f"\n[{session_id}] ═══ FAST PATCH MODE ═══════════════════")
            old_files = load_files(session_id)
            if old_files:
                saved["files"] = old_files
            saved["task"] = task
            saved["status"] = "running"
            saved["conversation_mode"] = True
            saved["mode"] = "fast"
            saved["iterations"] = 0
            saved["max_iterations"] = max_retries
            saved["current_phase"] = "fast"
            hist = saved.get("history", [])
            hist = _history_append(hist, "user", "User", task)
            saved["history"] = hist
            graph = get_fast_graph()
            for event in graph.stream(saved, config={"recursion_limit": 100}):
                for node_name, data in event.items():
                    yield {"node": node_name, "data": data}
        elif mode == "auto":
            # Auto mode: smart router decides chat vs plan vs fast
            print(f"\n[{session_id}] ═══ AUTO MODE (smart routing) ═══════════")
            init = _make_init_state(task, session_id, provider, refine_prompt, max_retries)
            init["mode"] = "auto"

            graph = get_auto_graph()
            for event in graph.stream(init, config={"recursion_limit": 100}):
                for node_name, data in event.items():
                    yield {"node": node_name, "data": data}
        else:
            print(f"\n[{session_id}] ═══ NEW PROJECT MODE (mode={mode}) ═══════════")
            init = _make_init_state(task, session_id, provider, refine_prompt, max_retries)
            init["mode"] = mode

            graph = get_graph()
            for event in graph.stream(init, config={"recursion_limit": 100}):
                for node_name, data in event.items():
                    yield {"node": node_name, "data": data}


def continue_task(
    session_id: str,
    user_guidance: str,
    provider: str = "auto",
    max_retries: int = 6,
):
    """Continue a failed/paused task with user guidance. Yields node events."""
    saved = load_state(session_id)
    if not saved:
        yield {"node": "__error__", "data": {"error": "No saved state found for this session."}}
        return

    # Also load files from disk
    disk_files = load_files(session_id)
    if disk_files:
        saved["files"] = disk_files

    # Update state for continuation
    saved["user_guidance"] = user_guidance
    saved["status"] = "running"
    saved["max_iterations"] = saved.get("iterations", 0) + max_retries
    saved["consecutive_same_errors"] = 0
    saved["current_phase"] = "deps_done"  # Resume from implementer

    saved["history"] = _history_append(
        saved.get("history", []), "user", "User",
        f"**Additional guidance:** {user_guidance}"
    )

    graph = get_graph()
    for event in graph.stream(saved, config={"recursion_limit": 100}):
        for node_name, data in event.items():
            yield {"node": node_name, "data": data}


def refine_prompt_only(task: str, provider: str = "auto") -> str:
    """Refine a prompt without running the full pipeline."""
    llm, _ = get_llm(provider)
    return invoke_llm(llm, [
        SystemMessage(content=_SYS_REFINER),
        HumanMessage(content=task),
    ])

"""
retry_controller.py — Smart retry ladder with escalating strategies.
"""
from __future__ import annotations


# ─────────────────────────────────────────────────────────────
# Retry Strategies
# ─────────────────────────────────────────────────────────────

STRATEGIES = [
    "syntax_fix",        # attempt 1: minimal fix
    "dependency_fix",    # attempt 2: fix/install packages
    "logic_fix",         # attempt 3: re-analyse full code against spec
    "architecture_fix",  # attempt 4: regenerate plan + affected files
    "full_regen",        # attempt 5: fresh generation with failure context
    "full_regen_enriched",  # attempt 6: regen with enriched prompt from all failures
]


def decide_retry(
    iterations: int,
    max_iterations: int,
    exec_report: dict,
    review: dict,
    retry_history: list[dict],
) -> dict:
    """
    Decide the retry strategy based on current state.

    Returns {
        "action": "retry" | "pass" | "awaiting_user",
        "strategy": str,          # which strategy to use
        "route_to": str,          # graph node to route back to
        "diagnosis": str,         # human-readable diagnosis
        "retry_entry": dict,      # entry to append to retry_history
    }
    """
    verdict = review.get("verdict", "RETRY")

    # PASS → we're done
    if verdict == "PASS":
        return {
            "action": "pass",
            "strategy": "none",
            "route_to": "end",
            "diagnosis": "Review passed.",
            "retry_entry": None,
        }

    # Exceeded max iterations → ask user
    if iterations >= max_iterations:
        return {
            "action": "awaiting_user",
            "strategy": "awaiting_user",
            "route_to": "end",
            "diagnosis": _build_failure_summary(retry_history, review),
            "retry_entry": {
                "attempt": iterations + 1,
                "error_type": exec_report.get("error_type", "unknown"),
                "diagnosis": "Max retries reached. Awaiting user guidance.",
                "fix_applied": "none",
                "strategy": "awaiting_user",
            },
        }

    # Determine strategy
    error_type = exec_report.get("error_type", "unknown")
    strategy = _select_strategy(iterations, error_type, retry_history)
    route_to = _strategy_to_route(strategy)

    # Check for repeated errors — auto-escalate
    if _is_repeated_error(retry_history, error_type):
        idx = STRATEGIES.index(strategy) if strategy in STRATEGIES else 0
        if idx < len(STRATEGIES) - 1:
            strategy = STRATEGIES[min(idx + 1, len(STRATEGIES) - 1)]
            route_to = _strategy_to_route(strategy)
            print(f"  [RetryCtrl] Auto-escalated to {strategy} (repeated {error_type} error)")

    diagnosis = _build_diagnosis(exec_report, review, strategy)

    return {
        "action": "retry",
        "strategy": strategy,
        "route_to": route_to,
        "diagnosis": diagnosis,
        "retry_entry": {
            "attempt": iterations + 1,
            "error_type": error_type,
            "diagnosis": diagnosis,
            "fix_applied": strategy,
            "strategy": strategy,
            "scores": review.get("scores", {}),
            "feedback": review.get("feedback", ""),
        },
    }


# ─────────────────────────────────────────────────────────────
# Strategy Selection
# ─────────────────────────────────────────────────────────────

def _select_strategy(iterations: int, error_type: str, retry_history: list[dict]) -> str:
    """Select strategy based on iteration and error type."""

    # Override based on error type
    if error_type == "import" and iterations < 2:
        return "dependency_fix"
    if error_type == "syntax" and iterations < 2:
        return "syntax_fix"

    # Default ladder
    if iterations < len(STRATEGIES):
        return STRATEGIES[iterations]

    return "full_regen_enriched"


def _strategy_to_route(strategy: str) -> str:
    """Map a retry strategy to the graph node to route back to."""
    return {
        "syntax_fix": "file_generator",
        "dependency_fix": "dependency_installer",
        "logic_fix": "file_generator",
        "architecture_fix": "planner",
        "full_regen": "file_generator",
        "full_regen_enriched": "file_generator",
        "awaiting_user": "end",
    }.get(strategy, "file_generator")


def _is_repeated_error(retry_history: list[dict], current_error_type: str) -> bool:
    """Check if the same error type appeared in the last 2 attempts."""
    if len(retry_history) < 2:
        return False
    recent = [r.get("error_type") for r in retry_history[-2:]]
    return all(e == current_error_type for e in recent)


# ─────────────────────────────────────────────────────────────
# Diagnosis
# ─────────────────────────────────────────────────────────────

def _build_diagnosis(exec_report: dict, review: dict, strategy: str) -> str:
    """Build a human-readable diagnosis for the current failure."""
    parts = []

    error_summary = exec_report.get("error_summary", "")
    error_type = exec_report.get("error_type", "unknown")
    feedback = review.get("feedback", "")

    if error_summary:
        parts.append(f"Error: {error_summary}")
    if feedback:
        parts.append(f"Review: {feedback}")

    # Add strategy-specific notes
    strategy_notes = {
        "syntax_fix": "Fixing syntax errors. Minimal change.",
        "dependency_fix": "Fixing dependency issues. Will reinstall packages.",
        "logic_fix": "Re-analysing full code against spec. Logic-level fix.",
        "architecture_fix": "Regenerating plan and affected files.",
        "full_regen": "Full code regeneration with failure context.",
        "full_regen_enriched": "Full regeneration with all failure history as context.",
    }
    parts.append(f"Strategy: {strategy_notes.get(strategy, strategy)}")

    return " | ".join(parts)


def _build_failure_summary(retry_history: list[dict], review: dict) -> str:
    """Build a summary of all failures for the user."""
    parts = ["All automatic retries exhausted. Here is what happened:\n"]

    for r in retry_history:
        parts.append(
            f"  Attempt #{r.get('attempt', '?')}: "
            f"[{r.get('strategy', '?')}] "
            f"{r.get('diagnosis', 'No diagnosis')}"
        )

    scores = review.get("scores", {})
    if scores:
        parts.append(f"\nLatest quality scores: {scores}")

    parts.append(
        f"\nLatest feedback: {review.get('feedback', 'None')}"
    )
    parts.append(
        "\nYou can provide additional guidance to help the system fix the issue. "
        "Describe what you think is wrong or what the code should do differently."
    )

    return "\n".join(parts)

"""
contracts.py — AutoDev v4 immutable data contracts.

All LLM agents produce/consume these Pydantic models.
SpecContract and PlanContract are frozen after creation — no agent may modify them.

v4 changes:
- Expanded output_type to support 17+ languages (C, C++, Java, Go, Rust, etc.)
- Added OutputCategory enum for execution validation strategy
- Added expected_output_files to SpecContract
- Added execution_command to PlanContract
- Expanded PlanContract.runtime to match all supported languages
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, field_validator, model_validator


# ─────────────────────────────────────────────────────────────
# Output Categories (v4 NEW) — How to validate execution
# ─────────────────────────────────────────────────────────────

class OutputCategory(str, Enum):
    """Determines how the executor validates success."""
    CLI_OUTPUT = "cli_output"           # Expects stdout/stderr text
    FILE_OUTPUT = "file_output"         # Expects generated files (images, CSV, PDF, etc.)
    GUI_OUTPUT = "gui_output"           # GUI app — timeout = success (stayed up)
    SERVER_OUTPUT = "server_output"     # Server/Streamlit — timeout = success
    BROWSER_OUTPUT = "browser_output"   # HTML — validate structure + screenshot
    COMPILATION_ONLY = "compilation_only"  # Just needs to compile (library)
    NO_OUTPUT = "no_output"             # Script with side effects, no visible output expected


# All supported output types
SUPPORTED_OUTPUT_TYPES = {
    "python", "html", "streamlit", "shell", "js", "typescript",
    "c", "cpp", "java", "go", "rust", "ruby", "php", "r",
    "kotlin", "swift", "other",
}

# All supported runtimes
SUPPORTED_RUNTIMES = {
    "python3", "node", "bash", "browser",
    "c", "cpp", "java", "go", "rust", "ruby", "php",
    "r", "kotlin", "swift", "typescript", "other",
}


# ─────────────────────────────────────────────────────────────
# Spec Contract (set by Spec Agent, IMMUTABLE thereafter)
# ─────────────────────────────────────────────────────────────

class SpecContract(BaseModel):
    """Immutable project specification produced by the Spec Agent."""
    model_config = ConfigDict(frozen=True)  # Immutable after creation

    problem_statement: str
    objective: str
    output_type: str  # v4: open string, validated against SUPPORTED_OUTPUT_TYPES
    expected_files: list[str]
    entrypoint: str
    dependencies: list[str] = []
    execution_target: str = "terminal"  # v4: relaxed from Literal
    acceptance_criteria: list[str] = []
    constraints: list[str] = []
    project_type: str = "script"
    # v4 NEW fields
    output_category: str = "cli_output"  # OutputCategory value
    expected_output_files: list[str] = []  # Files the program is expected to create

    @field_validator("output_type")
    @classmethod
    def validate_output_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_OUTPUT_TYPES:
            # Be lenient: map common aliases
            aliases = {
                "c++": "cpp", "cplusplus": "cpp", "cxx": "cpp",
                "javascript": "js", "node": "js", "nodejs": "js",
                "bash": "shell", "sh": "shell", "zsh": "shell",
                "ts": "typescript", "tsx": "typescript",
                "python3": "python", "py": "python",
                "golang": "go",
                "rs": "rust",
                "rb": "ruby",
                "webpage": "html", "website": "html", "web": "html",
            }
            v = aliases.get(v, v)
            if v not in SUPPORTED_OUTPUT_TYPES:
                v = "other"  # Don't reject, just classify as other
        return v

    @field_validator("expected_files")
    @classmethod
    def files_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("expected_files must contain at least one file")
        return v

    @field_validator("output_category")
    @classmethod
    def validate_output_category(cls, v: str) -> str:
        valid = {e.value for e in OutputCategory}
        if v not in valid:
            return OutputCategory.CLI_OUTPUT.value
        return v

    @model_validator(mode="after")
    def entrypoint_in_files(self) -> "SpecContract":
        if self.entrypoint not in self.expected_files:
            object.__setattr__(self, "expected_files", list(self.expected_files) + [self.entrypoint])
        return self

    @model_validator(mode="after")
    def infer_output_category(self) -> "SpecContract":
        """Auto-infer or OVERRIDE output_category from output_type.
        
        Known mappings are ALWAYS enforced (e.g. streamlit is ALWAYS server_output,
        never gui_output) regardless of what the LLM returned.
        """
        ot = self.output_type
        et = self.execution_target

        # ── Forced overrides for known types (always win) ──
        forced_map = {
            "streamlit": OutputCategory.SERVER_OUTPUT.value,
            "html": OutputCategory.BROWSER_OUTPUT.value,
        }
        if ot in forced_map:
            object.__setattr__(self, "output_category", forced_map[ot])
            return self

        # ── Infer from execution_target when output_category is default ──
        if self.output_category == "cli_output":
            target_map = {
                "browser": OutputCategory.BROWSER_OUTPUT.value,
                "gui": OutputCategory.GUI_OUTPUT.value,
                "streamlit": OutputCategory.SERVER_OUTPUT.value,
            }
            if et in target_map:
                object.__setattr__(self, "output_category", target_map[et])

        # ── Expected output files override everything to file_output ──
        if self.expected_output_files:
            object.__setattr__(self, "output_category", OutputCategory.FILE_OUTPUT.value)
        return self

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> "SpecContract":
        # Strip unknown fields gracefully
        known = set(cls.model_fields.keys())
        cleaned = {k: v for k, v in d.items() if k in known}
        return cls(**cleaned)


# ─────────────────────────────────────────────────────────────
# Plan Contract (set by Planner Agent, IMMUTABLE thereafter)
# ─────────────────────────────────────────────────────────────

class PlanContract(BaseModel):
    """Immutable build plan produced by the Planner Agent."""
    model_config = ConfigDict(frozen=True)

    project_structure: dict[str, str]  # filename → description
    file_order: list[str]              # dependency-first generation order
    packages: list[str] = []           # pip/npm/system packages
    runtime: str = "python3"           # v4: open string, validated
    entrypoint: str
    validation_strategy: str = "Run entrypoint and check for errors"
    test_strategy: str = "Syntax check all files"
    fallback_notes: str = ""
    # v4 NEW field
    execution_command: str = ""        # Explicit command to run the project

    @field_validator("runtime")
    @classmethod
    def validate_runtime(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in SUPPORTED_RUNTIMES:
            aliases = {
                "python": "python3", "py": "python3", "python3.12": "python3",
                "javascript": "node", "nodejs": "node", "npm": "node",
                "sh": "bash", "shell": "bash", "zsh": "bash",
                "c++": "cpp", "cplusplus": "cpp", "g++": "cpp", "gcc": "c",
                "golang": "go",
                "rs": "rust", "cargo": "rust",
                "rb": "ruby",
                "ts": "typescript",
            }
            v = aliases.get(v, v)
            if v not in SUPPORTED_RUNTIMES:
                v = "other"
        return v

    @model_validator(mode="after")
    def entrypoint_in_file_order(self) -> "PlanContract":
        if self.entrypoint not in self.file_order:
            object.__setattr__(self, "file_order", list(self.file_order) + [self.entrypoint])
        return self

    @model_validator(mode="after")
    def all_files_in_structure(self) -> "PlanContract":
        updated = dict(self.project_structure)
        for f in self.file_order:
            if f not in updated:
                updated[f] = f"Project file ({f})"
        object.__setattr__(self, "project_structure", updated)
        return self

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> "PlanContract":
        known = set(cls.model_fields.keys())
        cleaned = {k: v for k, v in d.items() if k in known}
        return cls(**cleaned)


# ─────────────────────────────────────────────────────────────
# File Patches (used by Implementer Agent on retries)
# ─────────────────────────────────────────────────────────────

class DiffHunk(BaseModel):
    start_line: int
    end_line: int
    old: str
    new: str

class FilePatch(BaseModel):
    filename: str
    action: Literal["create", "modify", "delete"]
    content: Optional[str] = None
    patches: Optional[list[DiffHunk]] = None

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> "FilePatch":
        return cls(**d)

class PatchSet(BaseModel):
    file_patches: list[FilePatch]

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> "PatchSet":
        return cls(**d)


# ─────────────────────────────────────────────────────────────
# Error Classification (produced by Error Classifier node)
# ─────────────────────────────────────────────────────────────

class ErrorClassification(BaseModel):
    """Root cause analysis of an execution or review failure."""

    error_type: Literal[
        "syntax", "import", "runtime", "logic",
        "architecture", "timeout", "dependency",
        "file_mismatch", "compilation", "unknown"
    ]
    root_cause: str
    affected_files: list[str] = []
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    suggested_strategy: Literal[
        "syntax_fix",       # Fix syntax in affected files
        "dependency_fix",   # Reinstall or fix dependencies
        "logic_fix",        # Fix logic in affected files
        "architecture_fix", # Replan (planner agent)
        "full_regen",       # Regenerate all files fresh
        "escalate",         # Human gate
    ] = "logic_fix"

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> "ErrorClassification":
        return cls(**{k: v for k, v in d.items() if k in cls.model_fields})

    @classmethod
    def unknown(cls, reason: str = "Classification failed") -> "ErrorClassification":
        return cls(
            error_type="unknown",
            root_cause=reason,
            affected_files=[],
            severity="medium",
            suggested_strategy="logic_fix",
        )


# ─────────────────────────────────────────────────────────────
# Review Rubric (produced by Reviewer Agent)
# ─────────────────────────────────────────────────────────────

class ReviewRubric(BaseModel):
    """Structured quality review with evidence for each dimension."""

    # Each dimension: 1‑10 score + evidence string
    spec_match: int
    spec_match_evidence: str = ""
    file_completeness: int
    file_completeness_evidence: str = ""
    runtime_correctness: int
    runtime_correctness_evidence: str = ""
    dependency_correctness: int
    dependency_correctness_evidence: str = ""
    output_quality: int
    output_quality_evidence: str = ""
    visual_aesthetics: Optional[int] = None
    visual_aesthetics_evidence: Optional[str] = None

    verdict: Literal["PASS", "RETRY"] = "RETRY"
    issues: list[str] = []
    files_to_fix: list[str] = []
    feedback: str = ""

    @model_validator(mode="after")
    def clamp_and_decide_verdict(self) -> "ReviewRubric":
        from .config import Config
        dims = ["spec_match", "file_completeness", "runtime_correctness",
                "dependency_correctness", "output_quality"]
        for d in dims:
            val = getattr(self, d)
            clamped = max(1, min(10, int(val)))
            object.__setattr__(self, d, clamped)

        if self.visual_aesthetics is not None:
            clamped_vis = max(1, min(10, int(self.visual_aesthetics)))
            object.__setattr__(self, "visual_aesthetics", clamped_vis)

        # PASS requires ALL dimensions >= threshold
        scores = [getattr(self, d) for d in dims]
        if self.visual_aesthetics is not None:
            scores.append(self.visual_aesthetics)
            
        all_pass = all(s >= Config.MIN_REVIEW_SCORE for s in scores)
        verdict = "PASS" if all_pass else "RETRY"
        object.__setattr__(self, "verdict", verdict)
        return self

    @property
    def average_score(self) -> float:
        scores = list(self.scores_dict.values())
        return round(sum(scores) / len(scores), 1)

    @property
    def scores_dict(self) -> dict:
        d = {
            "spec_match": self.spec_match,
            "file_completeness": self.file_completeness,
            "runtime_correctness": self.runtime_correctness,
            "dependency_correctness": self.dependency_correctness,
            "output_quality": self.output_quality,
        }
        if self.visual_aesthetics is not None:
            d["visual_aesthetics"] = self.visual_aesthetics
        return d

    def to_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_dict(cls, d: dict) -> "ReviewRubric":
        return cls(**{k: v for k, v in d.items() if k in cls.model_fields})

    @classmethod
    def failure_fallback(cls, exec_success: bool, error_summary: str = "",
                         output_category: str = "cli_output") -> "ReviewRubric":
        """Fallback rubric when LLM review fails."""
        base = 7 if exec_success else 3
        # For file_output programs, if execution succeeded, output quality is OK
        oq = base
        if exec_success and output_category == "file_output":
            oq = 8  # File was created successfully
        return cls(
            spec_match=base,
            file_completeness=base,
            runtime_correctness=9 if exec_success else 2,
            runtime_correctness_evidence=error_summary or ("Execution succeeded" if exec_success else "Execution failed"),
            dependency_correctness=base,
            output_quality=oq,
            feedback=f"Fallback review. Execution {'succeeded' if exec_success else 'failed'}. {error_summary}",
            issues=[error_summary] if error_summary and not exec_success else [],
        )


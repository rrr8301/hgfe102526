"""numagent — an agentic harness for building and testing numerical repositories.

Phase 1 (this): a deterministic detect -> build -> test -> report pipeline whose
functions double as the tool layer for Phase 2 (Claude Agent SDK).
"""
from .models import (
    BuildSystem,
    CommandResult,
    DetectResult,
    Language,
    RunReport,
    StepResult,
)
from .pipeline import run_pipeline

__version__ = "0.1.0"
__all__ = [
    "run_pipeline",
    "BuildSystem",
    "Language",
    "DetectResult",
    "StepResult",
    "CommandResult",
    "RunReport",
]

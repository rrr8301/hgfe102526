from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class Language(str, Enum):
    PYTHON = "python"
    C = "c"
    CPP = "cpp"
    FORTRAN = "fortran"
    UNKNOWN = "unknown"


class BuildSystem(str, Enum):
    PIP = "pip"            # pyproject.toml / setup.py
    CMAKE = "cmake"        # CMakeLists.txt
    MAKE = "make"          # bare Makefile
    FPM = "fpm"            # Fortran package manager
    UNKNOWN = "unknown"


@dataclass
class CommandResult:
    """Result of a single shell command."""
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    cwd: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class DetectResult:
    language: Language
    build_system: BuildSystem
    confidence: float                    # 0..1
    evidence: List[str] = field(default_factory=list)   # files/signals that led here
    missing_tools: List[str] = field(default_factory=list)  # toolchain gaps


@dataclass
class StepResult:
    """A named phase (build/test) that may run several commands."""
    name: str
    success: bool
    commands: List[CommandResult] = field(default_factory=list)
    summary: str = ""
    # test-specific, populated when parseable
    tests_passed: Optional[int] = None
    tests_failed: Optional[int] = None
    skipped: bool = False


@dataclass
class RunReport:
    repo_path: str
    detect: DetectResult
    build: Optional[StepResult] = None
    test: Optional[StepResult] = None

    @property
    def success(self) -> bool:
        b = self.build.success if self.build else True
        t = self.test.success if self.test else True
        return b and t


def to_dict(obj: Any) -> Any:
    """Recursively convert dataclasses/enums to JSON-safe primitives."""
    if dataclasses.is_dataclass(obj):
        return {k: to_dict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_dict(v) for v in obj]
    return obj

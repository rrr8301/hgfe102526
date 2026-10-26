from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from .models import BuildSystem, CommandResult, DetectResult, StepResult
from .shell import parse_pytest_counts, run

_VENV_DIR = ".numagent_venv"


def _venv_python(root: str) -> str:
    """Path to the per-repo virtualenv interpreter (isolated build/test env)."""
    return str(Path(root) / _VENV_DIR / "bin" / "python")


def _ensure_venv(root: str, timeout_s: float) -> Tuple[str, Optional[CommandResult]]:
    """Return (venv_python, creation_result). creation_result is None if the
    venv already existed. Isolating each repo avoids dependency clashes and
    keeps the system Python clean."""
    vp = _venv_python(root)
    if Path(vp).exists():
        return vp, None
    c = run([sys.executable, "-m", "venv", str(Path(root) / _VENV_DIR)], cwd=root, timeout_s=timeout_s)
    return vp, c


def _step(name: str, cmds: List[CommandResult], summary: str = "") -> StepResult:
    """Success requires every command to pass — use for dependent sequences
    (e.g. cmake configure THEN build)."""
    success = all(c.ok for c in cmds) and len(cmds) > 0
    return StepResult(name=name, success=success, commands=cmds, summary=summary)


def _try_strategies(name: str, strategies, cwd, timeout_s, summary):
    """Run fallback strategies in order, stop at the first success.

    Used where several commands are alternatives, not a sequence (e.g. pip
    editable-then-plain install). Success = the last attempt succeeded.
    """
    cmds: List[CommandResult] = []
    for cmd in strategies:
        c = run(cmd, cwd=cwd, timeout_s=timeout_s)
        cmds.append(c)
        if c.ok:
            break
    step = StepResult(name=name, success=cmds[-1].ok, commands=cmds, summary=summary)
    return step


def _skipped(name: str, why: str) -> StepResult:
    return StepResult(name=name, success=False, skipped=True, summary=why)


# --------------------------------------------------------------------------- #
# BUILD
# --------------------------------------------------------------------------- #
def build(repo_path: str, det: DetectResult, timeout_s: float = 900.0) -> StepResult:
    if det.missing_tools:
        return _skipped("build", f"missing toolchain: {', '.join(det.missing_tools)}")

    bs = det.build_system
    root = repo_path

    if bs == BuildSystem.PIP:
        vp, created = _ensure_venv(root, timeout_s)
        cmds: List[CommandResult] = []
        if created is not None:
            cmds.append(created)
            if not created.ok:
                return StepResult("build", False, cmds, "venv creation failed")
        # Modern pip/setuptools inside the venv restores PEP 660 editable
        # installs even when the host pip is ancient.
        cmds.append(run([vp, "-m", "pip", "install", "-q", "--upgrade",
                         "pip", "setuptools", "wheel"], cwd=root, timeout_s=timeout_s))
        # pytest lives in the venv too, so tests run hermetically.
        cmds.append(run([vp, "-m", "pip", "install", "-q", "pytest"], cwd=root, timeout_s=timeout_s))
        # Install the package: editable first, plain as fallback.
        install = _try_strategies(
            "build",
            [[vp, "-m", "pip", "install", "-e", "."], [vp, "-m", "pip", "install", "."]],
            cwd=root, timeout_s=timeout_s, summary="",
        )
        cmds.extend(install.commands)
        success = all(c.ok for c in cmds[:-len(install.commands)]) and install.success
        return StepResult("build", success, cmds, "venv + pip install (editable, then plain)")

    if bs == BuildSystem.CMAKE:
        build_dir = str(Path(repo_path) / "build")
        cfg = run(["cmake", "-S", ".", "-B", "build"], cwd=root, timeout_s=timeout_s)
        cmds = [cfg]
        if cfg.ok:
            cmds.append(run(["cmake", "--build", "build", "-j"], cwd=root, timeout_s=timeout_s))
        return _step("build", cmds, f"cmake configure+build in {build_dir}")

    if bs == BuildSystem.MAKE:
        return _step("build", [run(["make"], cwd=root, timeout_s=timeout_s)], "make")

    if bs == BuildSystem.FPM:
        return _step("build", [run(["fpm", "build"], cwd=root, timeout_s=timeout_s)], "fpm build")

    return _skipped("build", f"no builder for build system '{bs.value}'")


# --------------------------------------------------------------------------- #
# TEST
# --------------------------------------------------------------------------- #
_PYTEST_SUMMARY = re.compile(r"(?:(\d+) failed)?[, ]*(?:(\d+) passed)?", re.I)


def test(repo_path: str, det: DetectResult, timeout_s: float = 900.0) -> StepResult:
    if det.missing_tools:
        return _skipped("test", f"missing toolchain: {', '.join(det.missing_tools)}")

    bs = det.build_system
    root = repo_path

    if bs == BuildSystem.PIP:
        # Prefer the per-repo venv built in build(); fall back to host python.
        vp = _venv_python(root)
        py = vp if Path(vp).exists() else "python3"
        c = run([py, "-m", "pytest", "-q"], cwd=root, timeout_s=timeout_s)
        step = _step("test", [c], "pytest (venv)" if py == vp else "pytest (host)")
        p, f = parse_pytest_counts(c.stdout + c.stderr)
        step.tests_passed, step.tests_failed = p, f
        # pytest exit code 5 = "no tests collected"; treat as skip, not failure.
        if c.exit_code == 5:
            step.success = False
            step.skipped = True
            step.summary = "pytest collected no tests"
        return step

    if bs == BuildSystem.CMAKE:
        build_dir = Path(repo_path) / "build"
        if not build_dir.exists():
            return _skipped("test", "no build/ dir (build likely failed)")
        c = run(["ctest", "--output-on-failure"], cwd=str(build_dir), timeout_s=timeout_s)
        step = _step("test", [c], "ctest")
        m = re.search(r"(\d+)%\s+tests\s+passed,\s+(\d+)\s+tests\s+failed", c.stdout)
        if m:
            step.tests_failed = int(m.group(2))
        if "No tests were found" in (c.stdout + c.stderr):
            step.skipped = True
            step.summary = "ctest found no tests"
        return step

    if bs == BuildSystem.MAKE:
        # Convention: `make test` or `make check` if the target exists.
        target = _make_test_target(root)
        if not target:
            return _skipped("test", "no 'test'/'check' target in Makefile")
        return _step("test", [run(["make", target], cwd=root, timeout_s=timeout_s)], f"make {target}")

    if bs == BuildSystem.FPM:
        return _step("test", [run(["fpm", "test"], cwd=root, timeout_s=timeout_s)], "fpm test")

    return _skipped("test", f"no tester for build system '{bs.value}'")


def _make_test_target(root: str) -> Optional[str]:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        mf = Path(root) / name
        if mf.exists():
            text = mf.read_text(errors="replace")
            for target in ("test", "check"):
                if re.search(rf"^{target}\s*:", text, re.M):
                    return target
    return None

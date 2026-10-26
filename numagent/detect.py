from __future__ import annotations

from pathlib import Path
from typing import List

from .models import BuildSystem, DetectResult, Language
from .shell import which

# Source-extension → language, for the fallback content scan.
_EXT_LANG = {
    ".py": Language.PYTHON,
    ".pyx": Language.PYTHON,
    ".c": Language.C,
    ".h": Language.C,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".f": Language.FORTRAN,
    ".f90": Language.FORTRAN,
    ".f95": Language.FORTRAN,
    ".f03": Language.FORTRAN,
    ".for": Language.FORTRAN,
}

# Toolchain each build system needs, for the missing-tools check.
_REQUIRED_TOOLS = {
    BuildSystem.PIP: ["python3"],
    BuildSystem.CMAKE: ["cmake"],
    BuildSystem.MAKE: ["make"],
    BuildSystem.FPM: ["fpm", "gfortran"],
}


def _count_languages(root: Path, limit: int = 5000) -> dict:
    counts: dict = {}
    seen = 0
    for p in root.rglob("*"):
        if seen >= limit:
            break
        if not p.is_file():
            continue
        # skip vendored / VCS noise
        parts = set(p.parts)
        if parts & {".git", "node_modules", "build", "_build", ".tox", "venv", ".venv"}:
            continue
        lang = _EXT_LANG.get(p.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
            seen += 1
    return counts


def detect(repo_path: str) -> DetectResult:
    root = Path(repo_path)
    evidence: List[str] = []

    def has(name: str) -> bool:
        found = (root / name).exists()
        if found:
            evidence.append(f"found {name}")
        return found

    # --- Build system: marker files take priority (highest signal) ---
    build_system = BuildSystem.UNKNOWN
    language = Language.UNKNOWN
    confidence = 0.0

    if has("pyproject.toml") or has("setup.py") or has("setup.cfg"):
        build_system, language, confidence = BuildSystem.PIP, Language.PYTHON, 0.95
    elif has("CMakeLists.txt"):
        build_system, confidence = BuildSystem.CMAKE, 0.9
        # language decided by source scan below
    elif has("fpm.toml"):
        build_system, language, confidence = BuildSystem.FPM, Language.FORTRAN, 0.9
    elif has("Makefile") or has("makefile") or has("GNUmakefile"):
        build_system, confidence = BuildSystem.MAKE, 0.75

    # --- Language: refine via source-file census ---
    counts = _count_languages(root)
    if counts:
        dominant = max(counts, key=counts.get)
        census = ", ".join(f"{k.value}:{v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
        evidence.append(f"source census ({census})")
        if language == Language.UNKNOWN:
            language = dominant
        # C vs C++ for CMake/Make: trust the census
        if build_system in (BuildSystem.CMAKE, BuildSystem.MAKE):
            language = dominant
    elif language == Language.UNKNOWN:
        confidence = min(confidence, 0.3)

    if build_system == BuildSystem.UNKNOWN:
        evidence.append("no recognized build-system marker file")

    # --- Missing-toolchain check ---
    missing = [t for t in _REQUIRED_TOOLS.get(build_system, []) if not which(t)]

    return DetectResult(
        language=language,
        build_system=build_system,
        confidence=round(confidence, 2),
        evidence=evidence,
        missing_tools=missing,
    )

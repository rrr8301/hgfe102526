"""Era-aware numerical dependency reconstruction (DP6).

Three kinds of constraint, kept apart because the Strategy Generator must know
which is which:

1. **explicit**   — a version the repository or the PR documents.
2. **era**        — what could have been installed at the PR's date. Derived
                    here from release dates, not from a model's memory.
3. **numerical**  — evidence that a dependency, backend, dtype or build flag
                    changes the reported numerical behaviour.

A candidate version is never presented as a documented one: `documented: false`
travels with it, and the strategy prompt prints the two lists separately.
"""
from __future__ import annotations

import re
from datetime import date

#: Major releases whose arrival changes numerical behaviour or the build, with
#: the date they landed. Used to bound a candidate version by the PR's era — a
#: commit from 2024-02 cannot have been built against numpy 2.0.
#: Dates are release dates; the list is deliberately short and auditable rather
#: than a mirror of PyPI.
ERA_TABLE: dict[str, list[tuple[str, date]]] = {
    "numpy":   [("1.20", date(2021, 1, 30)), ("1.22", date(2021, 12, 31)),
                ("1.24", date(2022, 12, 18)), ("1.26", date(2023, 9, 16)),
                ("2.0", date(2024, 6, 16)), ("2.1", date(2024, 8, 18)),
                ("2.2", date(2024, 12, 8)), ("2.3", date(2025, 6, 7))],
    "scipy":   [("1.8", date(2022, 2, 5)), ("1.10", date(2023, 1, 3)),
                ("1.12", date(2024, 1, 20)), ("1.14", date(2024, 6, 24)),
                ("1.15", date(2025, 1, 3))],
    "pandas":  [("1.5", date(2022, 9, 19)), ("2.0", date(2023, 4, 3)),
                ("2.2", date(2024, 1, 19)), ("3.0", date(2025, 9, 1))],
    "python":  [("3.9", date(2020, 10, 5)), ("3.10", date(2021, 10, 4)),
                ("3.11", date(2022, 10, 24)), ("3.12", date(2023, 10, 2)),
                ("3.13", date(2024, 10, 7))],
    "cython":  [("3.0", date(2023, 7, 17))],
    "llvmlite": [("0.42", date(2024, 1, 31)), ("0.44", date(2025, 1, 15))],
}

#: Packages whose version visibly changes numerical results or dtype semantics,
#: as opposed to packages that merely need to import. Flagged so the strategy
#: can pin them deliberately.
NUMERICALLY_CONSEQUENTIAL = {
    "numpy", "scipy", "pandas", "mpmath", "sympy", "numba", "llvmlite", "jax",
    "jaxlib", "scikit-learn", "sklearn", "statsmodels", "cvxpy", "torch",
    "tensorflow", "openblas", "mkl", "blas", "lapack", "gmp", "mpfr", "flint",
}

_VERSION = re.compile(r"(\d+)\.(\d+)")


def _key(v: str) -> tuple:
    m = _VERSION.search(v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def era_ceiling(package: str, when: date | None) -> str:
    """The newest release of `package` that existed at `when`, or "" if unknown.

    This is the bound that matters for a historical build: anything newer could
    not have been installed at the commit, and installing it now is how a
    reproduction quietly becomes a different experiment.
    """
    table = ERA_TABLE.get((package or "").lower())
    if not table or when is None:
        return ""
    available = [v for v, released in table if released <= when]
    return max(available, key=_key) if available else ""


def era_constraints(dependencies: list, when: date | None) -> list:
    """`[{package, documented_constraint, era_ceiling, numerically_relevant, kind}]`.

    `kind` is the DP6 distinction, carried per dependency:
        explicit   the repository or PR states the constraint
        era        no statement; the ceiling is inferred from the PR's date
        unbounded  neither — the strategy may choose, and should say it chose
    """
    out = []
    for dep in dependencies or []:
        # callers outside the miner may hand this a list of requirement strings
        if isinstance(dep, str):
            # "numpy==2.1.0" is a requirement, not a package name
            m = re.match(r"\s*([A-Za-z0-9._+-]+)\s*(.*)", dep)
            dep = {"name": m.group(1), "constraint": (m.group(2) or "").strip() or None} \
                if m else {"name": ""}
        if not isinstance(dep, dict):
            continue
        name = str(dep.get("name") or "").strip()
        if not name:
            continue
        documented = bool(dep.get("documented")) and bool(dep.get("constraint"))
        ceiling = era_ceiling(name, when)
        numerical = bool(dep.get("numerically_relevant")) or (
            name.lower() in NUMERICALLY_CONSEQUENTIAL)
        out.append({
            "package": name,
            "documented_constraint": dep.get("constraint") if documented else None,
            "era_ceiling": ceiling or None,
            "numerically_relevant": numerical,
            "kind": "explicit" if documented else ("era" if ceiling else "unbounded"),
            "source": dep.get("source", ""),
        })
    return out


def summarise(constraints: list, when: date | None) -> list:
    """Lines for the context's `era_constraints`, in the design's own shape."""
    lines = []
    if when:
        lines.append(f"PR date: {when.isoformat()}")
    for c in constraints:
        if c["kind"] == "explicit":
            lines.append(f"explicit: {c['package']} {c['documented_constraint']}"
                         + ("  [numerically relevant]" if c["numerically_relevant"] else ""))
        elif c["kind"] == "era":
            lines.append(f"era: {c['package']} <= {c['era_ceiling']} "
                         f"(newest release at the PR date)"
                         + ("  [numerically relevant]" if c["numerically_relevant"] else ""))
        else:
            lines.append(f"unbounded: {c['package']} — no documented constraint and no "
                         f"era table entry; the strategy must choose and say so")
    return lines


def parse_date(text: str) -> date | None:
    """A date from an ISO-8601 timestamp, or None."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(text or ""))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None

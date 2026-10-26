from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Sequence

from .shell import run

_FLOAT_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def extract_floats(text: str) -> List[float]:
    """Pull every numeric literal out of text (for comparing program output)."""
    out = []
    for tok in _FLOAT_RE.findall(text):
        try:
            out.append(float(tok))
        except ValueError:
            pass
    return out


def allclose_report(
    actual: Sequence[float],
    expected: Sequence[float],
    rtol: float = 1e-7,
    atol: float = 0.0,
) -> Dict:
    """Elementwise closeness (numpy.allclose semantics: |a-e| <= atol + rtol*|e|).

    Returns a report with the worst offender so a failure is actionable.
    """
    if len(actual) != len(expected):
        return {"passed": False, "reason": f"length mismatch: {len(actual)} vs {len(expected)}"}
    if not expected:
        return {"passed": True, "n": 0, "max_abs_err": 0.0, "max_rel_err": 0.0}

    max_abs = max_rel = 0.0
    worst_i = -1
    n_bad = 0
    for i, (a, e) in enumerate(zip(actual, expected)):
        if math.isnan(a) or math.isnan(e):
            n_bad += 1
            if worst_i < 0:
                worst_i = i
            continue
        abs_err = abs(a - e)
        rel_err = abs_err / abs(e) if e != 0 else abs_err
        tol = atol + rtol * abs(e)
        if abs_err > tol:
            n_bad += 1
        if abs_err > max_abs:
            max_abs, worst_i = abs_err, i
        max_rel = max(max_rel, rel_err)

    return {
        "passed": n_bad == 0,
        "n": len(expected),
        "n_mismatch": n_bad,
        "max_abs_err": max_abs,
        "max_rel_err": max_rel,
        "rtol": rtol,
        "atol": atol,
        "worst": None if worst_i < 0 else {
            "index": worst_i, "actual": actual[worst_i], "expected": expected[worst_i],
        },
    }


def convergence_order(
    sizes: Sequence[float],
    errors: Sequence[float],
    expected_order: Optional[float] = None,
    order_tol: float = 0.5,
) -> Dict:
    """Estimate the observed order of convergence p from (resolution, error) pairs.

    For a method of order p, error ~ C * h^p where h ~ 1/size. Fitting
    log(error) against log(size) gives slope -p, so p = -slope. Uses a plain
    least-squares fit (no numpy).
    """
    pts = [(s, e) for s, e in zip(sizes, errors) if s > 0 and e > 0]
    if len(pts) < 2:
        return {"passed": False, "reason": "need >= 2 positive (size, error) pairs"}

    xs = [math.log(s) for s, _ in pts]
    ys = [math.log(e) for _, e in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return {"passed": False, "reason": "degenerate sizes (no variation)"}
    slope = sxy / sxx
    order = -slope

    # R^2 of the fit — a poor fit means "order" is meaningless.
    intercept = my - slope * mx
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0

    result = {"observed_order": order, "r_squared": r2, "n_points": n}
    if expected_order is not None:
        result["expected_order"] = expected_order
        result["order_tol"] = order_tol
        result["passed"] = abs(order - expected_order) <= order_tol and r2 >= 0.9
    else:
        result["passed"] = r2 >= 0.9
    return result


def reproducibility(
    command: str,
    cwd: str,
    runs: int = 3,
    rtol: float = 1e-12,
    atol: float = 0.0,
    timeout_s: float = 300.0,
) -> Dict:
    """Run `command` several times and check the numeric output is stable.

    Extracts every float from stdout+stderr of each run and compares run 2..N
    against run 1 within tolerance. Catches nondeterminism (unseeded RNG, race
    conditions, uninitialized memory) that a pass/fail test won't.
    """
    baseline_nums: Optional[List[float]] = None
    max_drift = 0.0
    per_run = []
    for k in range(runs):
        res = run(command, cwd=cwd, timeout_s=timeout_s)
        nums = extract_floats(res.stdout + res.stderr)
        per_run.append({"exit_code": res.exit_code, "n_floats": len(nums)})
        if k == 0:
            baseline_nums = nums
            continue
        if len(nums) != len(baseline_nums):
            return {"passed": False, "reason": f"run {k+1} produced {len(nums)} numbers vs {len(baseline_nums)} in run 1",
                    "runs": per_run}
        rep = allclose_report(nums, baseline_nums, rtol=rtol, atol=atol)
        max_drift = max(max_drift, rep.get("max_abs_err", 0.0))
        if not rep["passed"]:
            return {"passed": False, "reason": "numeric output drifted between runs",
                    "max_abs_err": rep["max_abs_err"], "worst": rep.get("worst"), "runs": per_run}
    return {"passed": True, "runs": runs, "n_floats": len(baseline_nums or []),
            "max_abs_err_across_runs": max_drift, "rtol": rtol, "atol": atol}

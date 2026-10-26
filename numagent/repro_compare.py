from __future__ import annotations

import json
import math
import re
from collections import Counter

from .fp_accuracy import compute_ulp_distance, significant_digits_agreement
from .fp_special import classify_float, compare_special_values
from .numeric import allclose_report, extract_floats
from .repro_specs import python_package
from .shell import parse_test_counts

# reproduction statuses
REPRODUCED = "REPRODUCED"
#: the verdict a `--no-probes` run can earn: the reproducing test FAILS on buggy
#: and PASSES on fixed, and that is ALL the evidence this mode collects. Kept
#: apart from REPRODUCED (which also carries value-level evidence) and from
#: PARTIALLY_REPRODUCED (which means evidence was sought and not found) so a
#: test-oracle-only arm is never read as either.
F2P_REPRODUCED = "F2P_REPRODUCED"
PARTIALLY_REPRODUCED = "PARTIALLY_REPRODUCED"
NOT_REPRODUCED = "NOT_REPRODUCED"
SETUP_FAILURE = "SETUP_FAILURE"
INVALID_STRATEGY = "INVALID_STRATEGY"
AMBIGUOUS_RESULT = "AMBIGUOUS_RESULT"

# Why a SETUP_FAILURE happened. The status alone says "a revision could not run
# the test", which a failed build, a stale dependency, a bad test invocation and
# a timeout all produce alike — and they want different fixes: the harness, the
# era pins, the instance. Each failure carries one of these as `setup_cause`, so
# a batch can be split into what to fix without reading every log.
CAUSE_BUILD_FAILED = "build_failed"                  # a revision did not compile
CAUSE_PACKAGE_NOT_IMPORTABLE = "package_not_importable"   # built, then not importable
CAUSE_TEST_COLLECTION_ERROR = "test_collection_error"     # the runner died collecting
CAUSE_TEST_ERROR = "test_error"                      # the runner errored otherwise
CAUSE_TIMEOUT = "timeout"
CAUSE_SETUP_COMMANDS_FAILED = "setup_commands_failed"     # the agent's setup/build failed
CAUSE_NO_EXECUTABLE_PLAN = "no_executable_plan"      # the planner never produced a step
CAUSE_WORKSPACE_PREP_FAILED = "workspace_prep_failed"     # clone / checkout / patch
CAUSE_DRIVER_ERROR = "driver_error"                  # the reasoning loop crashed
CAUSE_TESTS_SKIPPED = "tests_skipped"                # every selected test skipped here
CAUSE_NOTHING_RAN = "nothing_ran"
CAUSE_UNKNOWN = "unknown"

# Lines that only say THAT something failed. pip wraps every build failure in
# them, so the first "error" line of a build log is almost never the reason.
_GENERIC_ERROR = re.compile(
    r"subprocess-exited-with-error|metadata-generation-failed|Failed building wheel"
    r"|Could not build wheels|Failed to build|did not run successfully"
    r"|^\W*note:|^\W*hint:|See above for"
    r"|^\W*(?:ERROR: )?usage: ", re.IGNORECASE)      # pytest's usage header
# Most specific first: a compiler's fatal error, then its located error
# (`file.c:348:35: error: …`), an exception naming its cause, pytest's `E ` line.
# A bare `…Warning:` comes LAST: a build log is full of deprecation warnings
# that fail nothing, and one of them must never stand in for the real error.
_ERROR_LINE = (
    re.compile(r"fatal error:.*"),
    re.compile(r"\S+:\d+(?::\d+)?: error: .+"),
    re.compile(r"\b[A-Z]\w*(?:Error|Exception): .+"),
    re.compile(r"^\s*E\s{2,}(\S.*)"),
    re.compile(r"\berror(?:\[\w+\])?:.+", re.IGNORECASE),
    re.compile(r"\bFAILED:? .+"),
    re.compile(r"\b[A-Z]\w*Warning: .+"),
)
# `[ \t]*\n?[ \t]*`: a reason that STARTS with a newline (mpmath's
# pytest.skip("\nSkipping test (pylab not available …)\n")) prints on the next line
_SKIP_REASON = re.compile(r"SKIPPED \[\d+\] [^\n]*?:\d+:[ \t]*\n?[ \t]*([^\n]+)")
_COLLECTION_ERROR = re.compile(
    r"errors? during collection|ERROR collecting|ImportError while loading conftest"
    r"|found no collectors", re.IGNORECASE)


def first_error_line(text: str) -> str:
    """The one line of a build or test log that says WHY it failed, or ""."""
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln).strip()
             for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln and not _GENERIC_ERROR.search(ln)]
    for pattern in _ERROR_LINE:
        for ln in lines:
            m = pattern.search(ln)
            if m:
                return m.group(m.lastindex or 0).strip()[:240]
    return ""

# per-label probe outcomes (the numerical oracle's verdict on one pair)
FLIPPED = "flipped"          # defect signal on buggy, gone on fixed — what we want
PERSISTENT = "persistent"    # signal on both — the fix did not remove it
# no pathology either side, but the two revisions recorded DIFFERENT values. For a
# wrong-value defect (a bad antiderivative, a lost digit) that difference is the
# whole symptom: nothing is NaN, nothing overflows, the answers simply disagree.
DIVERGED = "diverged"
ABSENT = "absent"            # signal on neither, and they agree — no evidence here
INVERTED = "inverted"        # signal only on fixed — probe or strategy is wrong
UNPAIRED = "unpaired"        # only one revision ran this label
UNUSABLE = "unusable"        # a side's probe never completed

# numpy emits these RuntimeWarnings for the IEEE FP exception classes.
_FP_EXC_PATTERNS = {
    "invalid": "invalid value encountered",
    "divide_by_zero": "divide by zero encountered",
    "overflow": "overflow encountered",
    "underflow": "underflow encountered",
}

# A probe that dies of one of these never exercised the numerics — the PROBE is
# broken, not the revision. Scoring it as a symptom would invent a defect (and,
# since a broken probe usually breaks on both revisions, hide a real one). Every
# other exception (FloatingPointError, OverflowError, ZeroDivisionError,
# LinAlgError, an assertion on a value, …) IS a numerical symptom.
_PROBE_ERRORS = {"ModuleNotFoundError", "ImportError", "SyntaxError",
                 "IndentationError", "FileNotFoundError", "NameError",
                 "PermissionError", "SystemExit"}

#: …and the injected helpers called wrongly. `probe(name, value)` and
#: `probe_iteration(**fields)` are the HARNESS's, not the revision's: a
#: "probe() missing 1 required positional argument: 'value'" is the agent
#: mis-calling the recorder, so nothing about the numerics was measured — yet it
#: raises TypeError, which is otherwise a genuine numerical symptom, and lands as
#: `persistent` on both revisions (cvxpy#3267).
_PROBE_API_MISUSE = re.compile(
    r"probe(?:_iteration)?\(\)\s+(?:missing|takes|got|accepts)\b"
    r"|positional argument(?:s)?\b.*\bprobe(?:_iteration)?\(\)", re.I)

# Those names are Python's. Every other language reports the same class of
# failure — the probe could not be loaded, resolved or compiled — under an
# exception type that carries no such name ("Error", "Exception"), so the text
# has to be read. Without this a JavaScript probe with a bad import counts as a
# numerical symptom on BOTH revisions, and the oracle reports `persistent`: the
# fix appears not to have worked, when nothing was ever measured.
#
# Deliberately NOT here: TypeError / "is not a function". A function missing on
# the buggy revision and present on the fixed one is exactly what some reported
# defects look like.
_PROBE_ERROR_TEXT = re.compile(
    r"Cannot find module|ERR_MODULE_NOT_FOUND|ERR_REQUIRE_ESM|MODULE_NOT_FOUND"
    r"|Cannot use import statement outside a module|Unexpected token .{0,20}export"
    r"|\bSyntaxError\b|\bReferenceError\b"
    r"|error TS\d{3,5}\b"                      # tsc
    r"|cannot find symbol|package \S+ does not exist"   # javac
    r"|unresolved import|error\[E0432\]|error\[E0433\]"   # rustc
    r"|cannot find package|undefined: \w+"      # go build
    r"|fatal error: .{0,60}No such file",       # cc / c++ include
    re.I)

# record names read as a residual rather than as an output value
_RESIDUAL_KEYS = {"residual", "residuals", "resid"}
# per-iteration field names read as the quantity that should be shrinking
_ITER_KEYS = ("residual", "error", "err", "delta", "norm", "gap", "step")

#: A TEST TARGET that will not build is a failing test, not a broken setup: the
#: reproducing test was applied to the buggy revision and could not be run there.
#: The library failing to build is a different thing entirely — that IS a broken
#: setup — so the target kind has to be part of the match.
_TEST_BUILD_FAILED = re.compile(
    r"could not compile [`'\"]?[\w.-]+[`'\"]? \((?:lib |bin |example )?test\b"   # cargo
    r"|error: could not compile .*\(test [\"']",
    re.I)

#: the test PROCESS died: SIGABRT/SIGFPE/SIGSEGV as 128+n or as a negative return
#: code, or the interpreter's own last words. scipy#21062's test_pro_rad1 aborts
#: on buggy ("Fatal Python error: Aborted") and passes on fixed.
_CRASH_CODES = (134, 136, 139, -6, -8, -11)
_CRASHED = re.compile(r"Fatal Python error|Segmentation fault|core dumped|"
                      r"Floating point exception", re.I)

DEFAULT_RTOL = 1e-7
DEFAULT_ATOL = 0.0
DEFAULT_RESIDUAL_TOL = 1e-9


# --------------------------------------------------------------------------- #
# test outcomes
# --------------------------------------------------------------------------- #
def parse_test_outcome(result: dict | None) -> dict:
    """Classify one execution result into a test status.

    status ∈ {pass, fail, error, no_tests, timeout, missing}. `result` is a
    run_shell_command record: {exit_code, output, timed_out, ...}.
    """
    if not result:
        return {"status": "missing", "failure_kind": "", "passed": None,
                "failed": None, "errors": None}
    code = result.get("exit_code")
    out = result.get("output") or ""
    low = out.lower()
    kind = ""                       # how it failed, when that changes the reading
    passed, failed = parse_test_counts(out)
    m = re.search(r"(\d+)\s+error", low)
    errors = int(m.group(1)) if m else None

    if result.get("timed_out") or code == 124:
        status = "timeout"
    elif code in _CRASH_CODES or _CRASHED.search(out):
        # before the summary checks: a crashed run prints none, which read as
        # "no tests ran" — and a crash on buggy alone was thrown away with it
        status, kind = "error", "crash"
    elif code == 5 or "no tests ran" in low or "no tests collected" in low:
        status = "no_tests"
    elif re.search(r"\b\d+ skipped", low) and not passed and not failed:
        # Every selected test was skipped — a missing optional dependency, most
        # often. Exit 0 makes that look like a pass, and a pass on both revisions
        # is how a good instance gets thrown away as NOT_REPRODUCED.
        status, kind = "no_tests", "skipped"
    elif passed == 0 and failed == 0:
        # the runner reported a summary and it is empty: the selector matched
        # nothing. Exit 0 makes that look like a pass, and a false pass on both
        # revisions is how a good instance gets thrown away as NOT_REPRODUCED.
        status = "no_tests"
    elif ("errors during collection" in low or "importerror" in low
          or "modulenotfounderror" in low) and not failed:
        status = "error"
    elif failed:
        status = "fail"
    elif code == 0:
        status = "pass"
    elif _TEST_BUILD_FAILED.search(out):
        # the test target itself does not build on this revision
        status, kind = "fail", "build"
    else:
        status = "error"
    # A sanitizer report in the code the fix changed IS the defect, whatever
    # pytest concluded: on x86 the overflow wraps, the value comes out right,
    # and the test passes — the report is the only place the bug shows.
    reports = result.get("sanitizer_reports") or []
    if reports and status in ("pass", "fail"):
        status, kind = "fail", "sanitizer"
    outcome = {"status": status, "exit_code": code, "failure_kind": kind,
               "passed": passed, "failed": failed, "errors": errors}
    if reports:
        outcome["sanitizer_reports"] = reports[:5]
    if kind == "skipped":
        # the runner's own reason — "Only works on GPU with capability >= sm90"
        # is an instance this machine cannot run, not a stale selector
        # EVERY distinct reason: variants of one test skip for different ones,
        # and the first ("Test requires TPUs, or interpret mode") can hide the
        # one that decides it ("DMAs not supported on TPU generations <= 3")
        reasons = list(dict.fromkeys(r.strip() for r in _SKIP_REASON.findall(out)))
        outcome["skip_reason"] = "; ".join(reasons)[:240]
    if status in ("error", "timeout"):
        # why the runner could not produce a verdict, in one line
        outcome["error_line"] = first_error_line(out)
        outcome["collection_error"] = bool(_COLLECTION_ERROR.search(out))
    return outcome


# --------------------------------------------------------------------------- #
# the numerical oracle: one probe capture -> one observation
# --------------------------------------------------------------------------- #
def _as_float(v) -> float | None:
    """Coerce one recorded scalar to a float ('nan'/'inf' survive JSON as text)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


_INT_TEXT = re.compile(r"^[+-]?\d+$")


def _as_int(v) -> int | None:
    """Coerce one recorded scalar to an EXACT integer, or None if it is not one.

    Kept apart from :func:`_as_float` because float64 cannot tell
    4611686018427387904 from 4611686018427387905 — and an int64 value corrupted
    through a float intermediate is exactly what astropy#20240 reports, so
    comparing those two as floats scores the corrupted value as the expected one.
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and _INT_TEXT.match(v.strip()):
        return int(v.strip())
    return None


def _numbers(value, out: list | None = None, depth: int = 0) -> list:
    """Flatten every number out of a recorded value (nested lists/dicts included)."""
    out = [] if out is None else out
    if depth > 6 or len(out) > 5000:
        return out
    f = _as_float(value)
    if f is not None:
        out.append(f)
    elif isinstance(value, dict):
        for v in value.values():
            _numbers(v, out, depth + 1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _numbers(v, out, depth + 1)
    return out


def _json_safe(v):
    """NaN/Inf are not valid JSON — carry them as their repr so an observation
    survives a round trip through the results file and the model's context."""
    if isinstance(v, float):
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, dict):
        return {k: _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


#: what the probe harness wraps a repeatedly-recorded name in
REPEATED = "__numagent_repeated__"


def _truncate_record(value, limit: int = 50):
    """Keep a recorded value readable: long sequences are cut to `limit` entries.

    A name the probe recorded many times keeps many more of its cases: they
    are the grid being compared, and a difference at case 120 must survive."""
    if isinstance(value, dict) and REPEATED in value and isinstance(value[REPEATED], list):
        cases = value[REPEATED]
        head = [_truncate_record(v, limit) for v in cases[:1000]]
        return {REPEATED: head + ([f"… {len(cases) - 1000} more"] if len(cases) > 1000 else [])}
    if isinstance(value, (list, tuple)):
        head = [_truncate_record(v, limit) for v in value[:limit]]
        return head + [f"… {len(value) - limit} more"] if len(value) > limit else head
    if isinstance(value, dict):
        return {k: _truncate_record(v, limit) for k, v in value.items()}
    if isinstance(value, str) and len(value) > 400:
        return value[:400] + "…"
    return value


def _special_state(values: list) -> dict:
    cats = Counter(classify_float(v)["category"] for v in values)
    n_inf = cats["+inf"] + cats["-inf"]
    return {"n_values": len(values), "categories": dict(cats),
            "has_nan": cats["nan"] > 0, "has_inf": n_inf > 0,
            "has_subnormal": cats["subnormal"] > 0,
            "has_negative_zero": cats["-zero"] > 0,
            "all_finite": (cats["nan"] + n_inf) == 0}


def _fp_exceptions(warnings_: list, stderr: str) -> dict:
    text = (" ".join(str(w) for w in warnings_) + " " + (stderr or "")).lower()
    return {k: (pat in text) for k, pat in _FP_EXC_PATTERNS.items()}


def _residual_state(records: dict, tol: float) -> dict | None:
    vals = [abs(v) for v in _numbers({k: v for k, v in records.items()
                                      if k.lower() in _RESIDUAL_KEYS})]
    if not vals:
        return None
    worst = max(vals)
    return {"n_values": len(vals), "max_abs_residual": worst, "tol": tol,
            "within_tol": math.isfinite(worst) and worst <= tol}


def _iteration_state(iterations: list, tol: float) -> dict | None:
    """Summarize an iteration trace: did the shrinking quantity actually shrink?"""
    if not iterations:
        return None
    seq = []
    for rec in iterations:
        if not isinstance(rec, dict):
            continue
        for key in _ITER_KEYS:
            if key in rec:
                f = _as_float(rec[key])
                if f is not None:
                    seq.append(abs(f))
                break
    n = len(iterations)
    if not seq:
        return {"n_iterations": n, "tracked": None,
                "converged": None, "diverged": None}
    first, last = seq[0], seq[-1]
    finite = math.isfinite(last)
    return {"n_iterations": n, "n_tracked": len(seq),
            "first": first, "final": last, "tol": tol,
            "converged": bool(finite and last <= tol),
            "diverged": bool(not finite or last > first),
            "monotone": all(b <= a for a, b in zip(seq, seq[1:])),
            "tail": seq[-10:]}


def _accuracy_state(values: list, expected: list, rtol: float, atol: float) -> dict:
    """Score observed values against a reference: tolerance, ULPs, digits."""
    exp = [e for e in (_as_float(x) for x in expected) if e is not None]
    obs = values[:len(exp)]
    rep = allclose_report(obs, exp, rtol=rtol, atol=atol)
    ulps, digits, mismatches = [], [], []
    for i, (e, o) in enumerate(zip(exp, obs)):
        u = compute_ulp_distance(e, o)["ulp_distance"]
        if u is not None:
            ulps.append(u)
        d = significant_digits_agreement(e, o)["correct_significant_digits"]
        if d is not None and math.isfinite(d):
            digits.append(d)
        cmp = compare_special_values(e, o, rtol=rtol, atol=atol)
        if not cmp["equal"]:
            mismatches.append({"index": i, "expected": repr(e), "observed": repr(o),
                               "note": cmp["note"]})
    return {"passed": rep.get("passed"), "reason": rep.get("reason"),
            "n_expected": len(exp), "n_observed": len(values),
            "max_abs_err": rep.get("max_abs_err"), "max_rel_err": rep.get("max_rel_err"),
            "worst": rep.get("worst"), "rtol": rtol, "atol": atol,
            "max_ulp_distance": max(ulps) if ulps else None,
            "min_correct_significant_digits": min(digits) if digits else None,
            "mismatches": mismatches[:10]}


_NO_MODULE = re.compile(r"No module named '([^']+)'")


def _missing_package(raw: dict, package: str) -> bool:
    """Did this capture die because the package under test is not importable?"""
    if not package:
        return False
    names = _NO_MODULE.findall(raw.get("traceback") or "")
    return bool(names) and names[-1] == package


def analyze_probe(raw: dict, *, label: str = "", expected: list | None = None,
                  rtol: float = DEFAULT_RTOL, atol: float = DEFAULT_ATOL,
                  residual_tol: float = DEFAULT_RESIDUAL_TOL, package: str = "") -> dict:
    """Turn one raw probe capture into a structured numerical observation.

    The ``pathology`` field is the oracle's answer for THIS run: is the numerical
    defect visible here (non-finite output, an IEEE FP exception, a residual or
    error above tolerance, a non-converging iteration, a raised exception)?
    ``present`` is None when the probe never completed, so a broken probe is
    never mistaken for a healthy one.

    `package` is the import name of the package under test. A probe that dies
    because THAT is missing is an ``environment_error``, not a ``probe_error``:
    no rewrite of the probe can make an unbuilt package importable.
    """
    status = raw.get("status") or "ok"
    exc_type = raw.get("exception_type") or ""
    records = raw.get("records") or {}
    # THE INVARIANT: a numerical pathology can only be inferred once the probe
    # actually reached the target operation. The runner reports that directly
    # (`target_reached`); the name/text heuristics below remain only for records
    # produced before it did, and for languages whose harness cannot say.
    reached = raw.get("target_reached")
    if status == "exception" and reached is False:
        status = "probe_error"
    elif status == "exception" and reached is None and (
            exc_type in _PROBE_ERRORS
            or _PROBE_ERROR_TEXT.search(raw.get("traceback") or "")
            or _PROBE_API_MISUSE.search(raw.get("traceback") or "")):
        status = "probe_error"          # the probe never reached the numerics
    # ...unless what it could not import is the package under test itself. Only
    # that exact top-level name counts: `cannot import name 'f' from 'astropy'`
    # is the probe's API mistake, `astropy.tabel` its typo, and a missing helper
    # library its own choice of import — a rewrite fixes all three.
    if status == "probe_error" and _missing_package(raw, package):
        status = "environment_error"
    usable = status in ("ok", "exception")

    value_records = {k: v for k, v in records.items() if k.lower() not in _RESIDUAL_KEYS}
    values = _numbers(value_records)
    source = "records"
    if not values and usable:                       # nothing numeric recorded: read stdout
        scraped = extract_floats(raw.get("stdout") or "")[:200]
        if scraped:                                 # keep 'records' when the scrape
            values, source = scraped, "stdout"      # found nothing either

    special = _special_state(values)
    fp_exc = _fp_exceptions(raw.get("warnings") or [], raw.get("stderr") or "")
    residual = _residual_state(records, residual_tol)
    iterations = _iteration_state(raw.get("iterations") or [], residual_tol)
    accuracy = _accuracy_state(values, expected, rtol, atol) if (expected and usable) else None

    def _last_traceback_line() -> str:
        lines = (raw.get("traceback") or "").strip().splitlines()
        return lines[-1][:160] if lines else exc_type or "exception"

    reasons: list[str] = []
    if not usable:
        reasons.append({
            "timeout": "probe timed out",
            "probe_error": f"the probe itself could not run ({_last_traceback_line()}) "
                           f"— fix the probe, not the revision",
            "environment_error": f"the revision cannot import {package!r}, the package "
                                 f"under test ({_last_traceback_line()}) — its build or "
                                 f"install is broken; fix the environment, not the probe",
        }.get(status, "probe produced no structured output"))
    else:
        if status == "exception":
            reasons.append(f"probe raised: {_last_traceback_line()}")
        if special["has_nan"]:
            reasons.append("produced NaN")
        if special["has_inf"]:
            reasons.append("produced Inf")
        reasons += [f"IEEE FP exception: {k}" for k, hit in fp_exc.items() if hit]
        if residual and not residual["within_tol"]:
            reasons.append(f"residual {residual['max_abs_residual']:.3e} > "
                           f"tol {residual['tol']:.3e}")
        if iterations and iterations.get("converged") is False:
            reasons.append(f"iteration did not converge in {iterations['n_iterations']} "
                           f"steps (final {iterations['final']:.3e})")
        if accuracy and accuracy["passed"] is False:
            rel = accuracy.get("max_rel_err")
            if accuracy.get("reason"):                       # e.g. a length mismatch
                detail = f" ({accuracy['reason']})"
            elif accuracy["mismatches"]:                     # NaN/Inf/signed-zero vs reference
                detail = f" ({accuracy['mismatches'][0]['note']})"
            elif isinstance(rel, float) and math.isfinite(rel):
                detail = f" (max_rel_err={rel:.3e})"
            else:
                detail = ""
            reasons.append(f"values do not match the reference{detail}")

    return _json_safe({
        "label": label, "status": status, "usable": usable,
        # where it failed, and whether the target was reached at all — the two
        # facts that decide whether an exception is evidence or noise
        "failure_stage": raw.get("failure_stage") or "",
        "target_reached": reached,
        "exit_code": raw.get("exit_code"), "timed_out": raw.get("timed_out", False),
        "runtime_s": raw.get("runtime_s"),
        "values": values[:50], "n_values": len(values), "values_source": source,
        # what the probe actually produced, name by name, and what it was asked
        # to match — kept side by side so a run can be audited after the fact
        "observed_records": {k: _truncate_record(v) for k, v in value_records.items()},
        "n_records": len(records),
        "expected": list(expected)[:50] if expected else None,
        "n_expected": len(expected) if expected else 0,
        "special": special, "fp_exceptions": fp_exc,
        "residual": residual, "iterations": iterations, "accuracy": accuracy,
        "pathology": {"present": (bool(reasons) if usable else None), "reasons": reasons},
        "traceback_tail": (raw.get("traceback") or "")[-800:],
        "stdout_tail": (raw.get("stdout") or "")[-800:],
        "stderr_tail": (raw.get("stderr") or "")[-800:],
    })


# --------------------------------------------------------------------------- #
# compare: buggy vs fixed
# --------------------------------------------------------------------------- #
def records_differ(b: dict | None, f: dict | None):
    """Do the two revisions disagree on what they recorded under the same names?

    Returns True/False, or None when the runs share no record name to compare.
    Values compare the way a reader would: numbers within tolerance, everything
    else on its normalized text, so ``x**2 + 1`` and ``x**2+1`` are one answer.
    """
    br = (b or {}).get("observed_records") or {}
    fr = (f or {}).get("observed_records") or {}
    shared = [k for k in br if k in fr]
    if not shared:
        return None
    return any(not _agrees(br[k], fr[k]) for k in shared)


def _pair_outcome(b: dict | None, f: dict | None) -> str:
    if b is None or f is None:
        return UNPAIRED
    if not b["usable"] or not f["usable"]:
        return UNUSABLE
    bp, fp = b["pathology"]["present"], f["pathology"]["present"]
    if bp and not fp:
        return FLIPPED
    if bp and fp:
        return PERSISTENT
    if fp and not bp:
        return INVERTED
    # neither run is pathological — but a wrong ANSWER is not a pathology, so ask
    # the only remaining question: did the two revisions produce the same values?
    return DIVERGED if records_differ(b, f) else ABSENT


#: the label `check_reported_examples` records under (repro_tools.EXAMPLES_LABEL).
#: That observation is ALREADY judged by compare_examples, against the values the
#: report claims. Scanning it for NaN/Inf as well asks a question it cannot
#: answer: on a bug whose fix is to PRODUCE NaN or inf (NaT propagation,
#: `linspace(inf, inf)`, `0 * inf` bounds) the correct post-fix value trips
#: `has_inf`, and the pair reads `persistent` — "the fix did not work" — about
#: the very value the examples oracle just confirmed as correct.
EXAMPLES_PROBE_LABEL = "reported_examples"


def aggregate_pairs(pairs: list):
    """The numerical verdict over judged pairs: the worst thing any pair saw.

    Shared by the live oracle, the examples-pair reconciliation and the
    re-grader, so a pair whose outcome is revised cannot leave the verdict
    behind.
    """
    outcomes = {p["outcome"] for p in pairs}
    if not pairs:
        return None
    if FLIPPED in outcomes and INVERTED in outcomes:
        return "mixed"
    if FLIPPED in outcomes:
        return FLIPPED
    if INVERTED in outcomes:
        return INVERTED
    if PERSISTENT in outcomes:
        return PERSISTENT
    if DIVERGED in outcomes:
        return DIVERGED
    if outcomes == {ABSENT}:
        return ABSENT
    return "inconclusive"


def compare_probes(buggy: dict, fixed: dict) -> dict:
    """The numerical oracle: pair probe observations by label and judge each pair.

    The examples-derived pair is reported but NOT aggregated: `compare_examples`
    owns that observation, and it compares against the report instead of
    pattern-matching for non-finite values.
    """
    pairs = []
    for label in sorted(set(buggy) | set(fixed)):
        b, f = buggy.get(label), fixed.get(label)
        pairs.append({
            "label": label,
            "outcome": _pair_outcome(b, f),
            "buggy": (b or {}).get("pathology"),
            "fixed": (f or {}).get("pathology"),
            # kept so a setup failure can tell "the probe was wrong" from "the
            # package was never importable" without re-reading the observations
            "buggy_status": (b or {}).get("status"),
            "fixed_status": (f or {}).get("status"),
            "values_differ": records_differ(b, f),
            "buggy_values": (b or {}).get("observed_records"),
            "fixed_values": (f or {}).get("observed_records"),
        })
    return {"n_pairs": len(pairs), "verdict": aggregate_pairs(pairs), "pairs": pairs}


# --------------------------------------------------------------------------- #
# the value oracle: mined input/output examples vs what the revisions printed
# --------------------------------------------------------------------------- #
# per-example outcomes
EX_REPRODUCED = "reproduced"      # buggy differs from expected, fixed matches it
EX_FIX_ONLY = "fix_only"          # both revisions match expected — no defect visible
EX_STILL_WRONG = "still_wrong"    # buggy differs and fixed does not match either
EX_INVERTED = "inverted"          # buggy matches expected, fixed does not
EX_NOT_OBSERVED = "not_observed"  # no probe recorded this example on some revision
EX_UNJUDGEABLE = "unjudgeable"    # the mined expected output is not a literal
# the report says the buggy revision already produced the expected value, so this
# example cannot separate the revisions whatever it observes
EX_NON_DISCRIMINATING = "non_discriminating"
#: neither reported value is a literal — but the two revisions answered this
#: example differently, which is the difference the report claims exists
EX_DIVERGED = "diverged"
#: …and the mirror: nothing literal to check against, and both agree
EX_SAME_ON_BOTH = "same_on_both"
#: some reported examples reproduce and others were never observed at all — the
#: report's claim is only partly checked, which is not the same as checked and met
EX_INCOMPLETE = "incomplete"

#: what a harness-generated example probe records when an expression raised
EXAMPLE_ERROR = "__error__: "
#: …and where it records the reported expectation, once evaluated in the revision
EXPECTED_PREFIX = "expected::"

_LITERALS = {"true": True, "false": False, "none": None, "null": None,
             "nan": float("nan"), "inf": float("inf"), "+inf": float("inf"),
             "-inf": float("-inf"), "infinity": float("inf"), "-infinity": float("-inf")}


def _norm_key(text) -> str:
    """Record names and example inputs compare on alphanumerics alone, so
    ``sec(z).is_zero`` and a probe that recorded ``sec_z_is_zero`` still pair."""
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


def _parse_expected(text):
    """The mined expected/buggy output as a comparable value.

    Returns ``(parsed, value)``. Mined outputs are prose as often as they are
    literals ("None or exception (property not implemented)"), and a prose
    expectation must be reported as unjudgeable rather than silently scored.
    """
    if isinstance(text, bool) or text is None or isinstance(text, (int, float)):
        return True, text
    raw = str(text).strip().strip("`'\"")
    if not raw:
        return False, None
    low = raw.lower()
    if low in _LITERALS:
        return True, _LITERALS[low]
    i = _as_int(raw)
    if i is not None:
        return True, i          # before float: 4611686018427387905 is not …904
    f = _as_float(raw)
    return (True, f) if f is not None else (False, None)


#: where each revision lives in the container. The same error raised from the
#: same file carries a different path on each side, and compared as text that
#: difference alone reads as the revisions answering differently.
_REVISION_DIR = re.compile(r"/workspace/(?:buggy|fixed)\b")


def _agrees(observed, expected) -> bool:
    """Does one observed value match an expected literal? Floats compare within
    the default tolerance; NaN matches NaN; True/False/None match by identity.

    Sequences and mappings compare element by element — `[1, 2, nan]` and
    `[1.0, 2.0, "nan"]` are one answer, which their text is not."""
    if isinstance(observed, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(observed) == len(expected) and all(
            _agrees(o, e) for o, e in zip(observed, expected))
    if isinstance(observed, dict) and isinstance(expected, dict):
        return observed.keys() == expected.keys() and all(
            _agrees(observed[k], expected[k]) for k in observed)
    if expected is None or isinstance(expected, bool) or observed is None \
            or isinstance(observed, bool):
        if isinstance(observed, str) and observed.strip().lower() in _LITERALS:
            observed = _LITERALS[observed.strip().lower()]
        return observed is expected
    io, ie = _as_int(observed), _as_int(expected)
    if io is not None and ie is not None:
        return io == ie            # exactly: these are the digits under test
    eo, ee = _as_float(observed), _as_float(expected)
    if eo is None or ee is None:
        return (_norm_key(_REVISION_DIR.sub("/workspace/rev", str(observed)))
                == _norm_key(_REVISION_DIR.sub("/workspace/rev", str(expected))))
    if math.isnan(ee) or math.isnan(eo):
        return math.isnan(ee) and math.isnan(eo)
    if math.isinf(ee) or math.isinf(eo):
        return eo == ee
    return abs(eo - ee) <= max(DEFAULT_RTOL * abs(ee), 1e-12)


# --------------------------------------------------------------------------- #
# the second look: a reported value the conservative parser could not read
#
# `_parse_expected` accepts a number, nan/inf or a keyword and calls everything
# else prose — so an example whose report is `'1d02m59s'`, `dtype('float64')`,
# `NaT` or `[1451692800000000000 0 3]` could only ever score `diverged`, however
# exactly the revisions answered it. These read those forms. They are applied
# ONLY to a row the first pass left resting on divergence (`_SECOND_LOOK`), so a
# loose reading can promote such a row and never demote a judged one.
# --------------------------------------------------------------------------- #
#: `array([...])`, `np.array([...])`, `tensor([...])` — the wrapper around a
#: printed sequence, which carries no information the elements do not
_ARRAY_WRAPPER = re.compile(
    r"^(?:np|numpy|jnp|torch|pd|cp)?\.?"
    r"(?:array|asarray|tensor|Series|Index|list|full)\s*\((.*)\)$", re.S)
#: a repr that names its type — `dtype('float64')`, `Period('1970-01-01', 'us')`,
#: `Timestamp('2016-01-02')`: not a number, but an exact string to match
_CALL_REPR = re.compile(r"^[A-Za-z_][\w.]*\s*\(.*\)$", re.S)
#: one bare token, which a report writes for a value with no numeric form:
#: `NaT`, `1d02m59s`, `285d`, `float64`
_TOKEN_LITERAL = re.compile(r"^[^\s,;=]+$")


def _split_sequence(inner: str):
    """The top-level elements of a printed sequence, or None if it is not one.

    Commas separate when there are any; whitespace separates when there are not,
    because numpy prints an array as `[0 0 1451779200000000000]`. Brackets and
    quotes nest, so `[[1 2] [3 4]]` splits into two rows and not four numbers."""
    items, depth, cur, quote = [], 0, "", ""
    by_space = "," not in inner
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = ""
            continue
        if ch in "'\"":
            quote, cur = ch, cur + ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return None
        if depth == 0 and (ch == "," or (by_space and ch.isspace())):
            if cur.strip():
                items.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if quote or depth:
        return None
    if cur.strip():
        items.append(cur.strip())
    return items


def _parse_rich(text):
    """The mined expected/buggy output as a comparable value, reading the forms
    :func:`_parse_expected` rejects. Returns ``(parsed, value)``.

    A string here is a value to match as text, not prose: only text that looks
    like one value — quoted, a typed repr, or a single token — is accepted, so a
    sentence still comes back unparsed.
    """
    parsed, value = _parse_expected(text)
    if parsed:
        return True, value
    if not isinstance(text, str) or not text.strip():
        return False, None
    raw = text.strip()
    quoted = len(raw) > 1 and raw[0] in "'\"`" and raw[-1] == raw[0]
    raw = raw.strip("`'\"").strip()
    wrapper = _ARRAY_WRAPPER.match(raw)
    if wrapper:
        return _parse_rich(wrapper.group(1).strip())
    if raw.startswith("[") and raw.endswith("]"):
        items = _split_sequence(raw[1:-1])
        if items is None:
            return False, None
        out = []
        for item in items:
            ok, v = _parse_rich(item)
            if not ok:
                return False, None
            out.append(v)
        return True, out
    if quoted or _CALL_REPR.match(raw) or (_TOKEN_LITERAL.match(raw) and len(raw) <= 60):
        return True, raw
    return False, None


def _unwrap_repeats(value):
    """The cases behind the probe harness's repeated-record wrapper."""
    if isinstance(value, dict) and len(value) == 1 and REPEATED in value:
        return value[REPEATED]
    return value


def _leaves(value, depth: int = 0) -> list:
    """Every scalar inside a recorded container."""
    if depth > 6:
        return []
    value = _unwrap_repeats(value)
    if isinstance(value, dict):
        return [x for v in value.values() for x in _leaves(v, depth + 1)]
    if isinstance(value, (list, tuple)):
        return [x for v in value for x in _leaves(v, depth + 1)]
    return [value]


def _agrees_rich(observed, expected) -> bool:
    """:func:`_agrees`, plus the container shapes a probe actually records.

    Unwraps the repeated-case wrapper, compares a dict of per-setting answers
    against a sequence by its values, and lets one expected value stand for every
    leaf of a container — which is what `np.full(5, inf)` and "all values should
    be 1" claim."""
    want_cases, cases = _unwrap_repeats(expected), _unwrap_repeats(observed)
    if cases is not observed and want_cases is not expected:
        # both sides swept the same cases — compare them case by case
        return len(cases) == len(want_cases) and all(
            _agrees_rich(c, w) for c, w in zip(cases, want_cases))
    if cases is not observed:
        # the same expression evaluated once per reported case, against ONE
        # reported answer: it is a claim about every case, not about the list
        return bool(cases) and all(_agrees_rich(c, expected) for c in cases)
    expected = want_cases
    if isinstance(observed, dict) and isinstance(expected, dict):
        return observed.keys() == expected.keys() and all(
            _agrees_rich(observed[k], expected[k]) for k in observed)
    if isinstance(observed, dict) and isinstance(expected, (list, tuple)):
        observed = list(observed.values())
    if isinstance(observed, (list, tuple)) and isinstance(expected, (list, tuple)):
        return len(observed) == len(expected) and all(
            _agrees_rich(o, e) for o, e in zip(observed, expected))
    if isinstance(observed, (list, tuple, dict)):
        leaves = _leaves(observed)
        return bool(leaves) and all(_agrees(v, expected) for v in leaves)
    if isinstance(expected, (list, tuple, dict)):
        return False
    return _agrees(observed, expected)


#: the names a numerical report uses for a value that has no digits
_VALUE_WORDS = {"nan", "inf", "-inf", "+inf", "infinity", "-infinity", "nat",
                "none", "null", "true", "false", "empty"}
#: numbers first, so `1.0` stays one token and `np.float64` becomes two
_TOKEN = re.compile(r"[-+]?\d+\.\d+(?:[eE][-+]?\d+)?|[-+]?\d+(?:[eE][-+]?\d+)?"
                    r"|[A-Za-z_][A-Za-z_0-9]*")


def _value_like(token: str) -> bool:
    """Is this token a VALUE rather than a word of prose?

    A number or a token carrying one (`float64`, `1d02m59s`), an identifier with
    an inner capital (`NaT`, `TypeError`, `OutOfBoundsTimedelta`), or one of the
    words a report writes in place of digits. "Returns", "should", "equals" are
    none of those, which is the point."""
    t = token.strip()
    if not t:
        return False
    return (t.lower() in _VALUE_WORDS or any(c.isdigit() for c in t)
            or any(c.isupper() for c in t[1:]))


def _value_tokens(value) -> set:
    """The value-like tokens in a line of prose or a recorded observation.

    Numbers compare by value, so the `1` of "all values should be 1" and an
    observed `1.0` are one token."""
    text = value if isinstance(value, str) else json.dumps(_json_safe(value), default=str)
    out = set()
    for t in _TOKEN.findall(text):
        if t.startswith("__"):                   # the harness's own wrappers
            continue
        if not _value_like(t):
            continue
        f = _as_float(t)
        out.add(repr(f) if f is not None and math.isfinite(f) else t.casefold())
    return out


#: `object`, `Int64`, `float32`, `datetime64[ns]`, `dtype('float64')` — what a
#: probe records when it reached for the dtype instead of the value
_DTYPE_NAME = re.compile(
    r"^(?:dtype\(.*\)|object|string|str|category|bool_?|"
    r"(?:u?int|float|complex)\d*|U?Int\d+|Float\d+|boolean|"
    r"(?:datetime|timedelta)64(?:\[\w+\])?)$")


def _is_dtype_name(value) -> bool:
    """Did the probe record a TYPE where the report names a number?

    pandas#63925 asks for `int(result[0])` and its probe recorded `object` on
    buggy and `Int64` on fixed. Neither is 10000000000000003, but neither is
    evidence against it either — the probe answered a different question, and
    scoring it as "the fix did not work" (`still_wrong`) condemns the instance on
    a row that measured nothing. Unobserved is what it is."""
    return isinstance(value, str) and bool(_DTYPE_NAME.match(value.strip()))


def _prose_recovers(prose, observed, other) -> bool:
    """Does the report's prose name a value THIS revision shows and the other
    does not?

    The last resort for an expectation written as a sentence — "all values should
    be 1", "value.dtype == np.float64", "Returns NaT", "True (cache hit)". The
    sentence still names the value, and when only one revision produced it the
    report's claim has been checked after all. Used in one direction only, to
    promote a `diverged` row, so a loose reading costs nothing that divergence
    had not already earned."""
    want = _value_tokens(prose)
    if not want or len(want) > 40:
        return False
    mine, theirs = _value_tokens(observed), _value_tokens(other)
    return bool((want & mine) - theirs) and not ((want & theirs) - mine)


def _revision_records(obs: dict, expected: bool = False) -> dict:
    """Every value the revision's probes recorded, name -> value.

    `expected` selects the reported expectations the probe evaluated instead of
    the observations; the two are kept apart so an expectation is never mistaken
    for what a revision produced.
    """
    out = {}
    for probe in (obs or {}).get("probes", {}).values():
        for name, value in (probe.get("observed_records") or {}).items():
            is_expected = str(name).startswith(EXPECTED_PREFIX)
            if is_expected == expected:
                key = str(name)[len(EXPECTED_PREFIX):] if expected else name
                # Two probes can answer the same example: the sweep, which
                # could not even compile a described one, and the probe written
                # for it. A harness failure never displaces a real answer.
                if key in out and _is_harness_error(value) and not _is_harness_error(out[key]):
                    continue
                out[key] = value
    return out


#: like :func:`_norm_key` but keeping the characters that carry VALUE — a sign,
#: a decimal point. Without them `np.float32(-1.0)` and `np.float32(1.0)`
#: normalise to the same key and an example is scored against its neighbour's
#: observation (jax#38106).
_SIGNED = re.compile(r"[^a-z0-9.+-]+")


def _sign_key(text) -> str:
    return _SIGNED.sub("", str(text).lower())


def _lookup_records(records: dict, example_input: str) -> dict:
    """EVERY record that answers this example, name -> value.

    A probe that sweeps one example over dtypes or settings records one entry per
    case — `…::<class 'numpy.float16'>`, `…::<class 'numpy.longdouble'>`. Taking
    only the first discards exactly the case that differs: on numpy#31433 the
    long-double record held the defect (True on buggy, False on fixed) while the
    float16 record, which sorted first, held False on both, and the example was
    scored `same_on_both` against evidence the run had already collected.

    Matching runs in tiers, strictest first, and the first tier that matches
    anything wins — so an exact name is never displaced by a loose one, and a
    sign is never normalised away while an exact-or-signed match exists.
    """
    if not records:
        return {}
    key_n, key_s = _norm_key(example_input), _sign_key(example_input)
    if not key_n:
        return {}
    #: what a probe appends to a record name to annotate it rather than to add a
    #: case: the value's repr, dtype, shape. They are not answers to the example,
    #: and comparing them as values turns "the dtype string matched" into
    #: evidence about a number.
    def _is_annotation(name) -> bool:
        return str(name).rsplit("::", 1)[-1].lower() in (
            "repr", "dtype", "shape", "type", "units", "warnings", "len")

    def _with_sweep(exact: dict) -> dict:
        """an exact match plus the `name::<case>` records that extend it"""
        out = dict(exact)
        for stem in list(exact):
            prefix = f"{stem}::"
            out.update({n: v for n, v in records.items() if str(n).startswith(prefix)})
        return out

    tiers = [
        _with_sweep({n: v for n, v in records.items() if str(n) == str(example_input)}),
        {n: v for n, v in records.items() if _sign_key(n) == key_s},
        {n: v for n, v in records.items()
         if key_s and (_sign_key(n).startswith(key_s) or key_s.startswith(_sign_key(n)))},
        {n: v for n, v in records.items() if _norm_key(n) == key_n},
        {n: v for n, v in records.items()
         if _norm_key(n) and (key_n in _norm_key(n) or _norm_key(n) in key_n)},
    ]
    def _values(chosen: dict) -> dict:
        values = {n: v for n, v in chosen.items() if not _is_annotation(n)}
        return values or chosen

    # A measured value anywhere beats a failure at a stricter tier: a sweep's
    # SyntaxError, or one probe's ValueError, must not hide the probe that
    # actually measured the example. Preference runs clean values first, then
    # anything that is at least not the harness's own failure, then whatever
    # matched — so a failure is reported only when nothing was measured.
    for keep in (lambda v: not _eval_error(v) and not _is_harness_error(v),
                 lambda v: not _is_harness_error(v),
                 lambda v: True):
        for tier in tiers:
            chosen = {n: v for n, v in tier.items() if keep(v)}
            if chosen:
                return _values(chosen)
    return {}


def _collapse(found: dict) -> tuple:
    """(name, value) for one example: the record itself when a single one answers
    it, and the whole swept set when several do — compared case by case against
    the other revision, and leaf by leaf against the reported value."""
    if not found:
        return None, None
    if len(found) == 1:
        return next(iter(found.items()))
    return f"{len(found)} records", dict(found)


def _lookup_record(records: dict, example_input: str):
    """Find the record that answers this example — by name, exactly if possible."""
    key = _norm_key(example_input)
    if not key:
        return None, None
    exact = [(n, v) for n, v in records.items() if _norm_key(n) == key]
    embedded = [(n, v) for n, v in records.items()          # a name that embeds the input
                if _norm_key(n) and _norm_key(n) != key
                and (_norm_key(n) in key or key in _norm_key(n))]
    # Every match is a candidate, and a real answer beats a harness failure:
    # the sweep's SyntaxError for a described example names it just as well as
    # the probe that actually measured it, and came first.
    for name, value in exact + embedded:
        if not _is_harness_error(value):
            return name, value
    return (exact + embedded)[0] if exact or embedded else (None, None)


#: Errors that belong to the HARNESS rather than to the revision: the example
#: text was not code at all, so nothing about the revision was measured. A
#: revision-level error (TypeError, ValueError, OverflowError) is the opposite —
#: it IS behaviour, and often the very behaviour a report claims.
_HARNESS_ERROR = re.compile(r"^\s*(SyntaxError|IndentationError|TabError)\b")

#: `AttributeError("module 'numpy' has no attribute 'in1d'")` recorded as a
#: plain string: an exception the probe formatted itself, with no origin.
_FORMATTED_EXCEPTION = re.compile(
    r"^\s*(?:raised:\s*)?[A-Za-z_]*(?:Error|Exception)\s*[(:]")


def _eval_error(value) -> str:
    """The error an example probe recorded in place of a value, if any."""
    from .repro_probe_langs import example_error_text, is_example_error
    if is_example_error(value):
        # tolerant of the marker's spacing: a generated probe writes it itself
        return example_error_text(value)[:200] or "evaluation failed"
    return ""


def _is_harness_error(value) -> bool:
    """Is this record the PROBE's failure rather than the revision's answer?

    Two ways it can be: the example text never compiled (a report written as
    prose), or the probe reached the target and then raised inside its OWN code
    — calling an API wrongly, using a numpy function removed years ago. Neither
    says anything about the revision, and scoring either as "the buggy side
    errored" manufactures a reproduction out of a broken probe.
    """
    if isinstance(value, dict) and "in_repository" in value:
        return not value.get("in_repository")
    if isinstance(value, str) and _FORMATTED_EXCEPTION.match(value):
        # An exception the probe formatted by hand carries no origin, so there
        # is no way to tell the revision's failure from the probe's. Unknown
        # provenance is not evidence — it is an example that went unobserved.
        return True
    return bool(_HARNESS_ERROR.match(_eval_error(value)))


def _example_outcome(b_match, f_match) -> str:
    if b_match is None or f_match is None:
        return EX_NOT_OBSERVED
    if not b_match and f_match:
        return EX_REPRODUCED
    if b_match and f_match:
        return EX_FIX_ONLY
    if b_match and not f_match:
        return EX_INVERTED
    return EX_STILL_WRONG


#: outcomes the second look may revise: the first pass found nothing in the
#: report it could compare against, so the row rests on divergence alone — or it
#: compared two int64s as float64 and could not see them apart.
_SECOND_LOOK = (EX_DIVERGED, EX_SAME_ON_BOTH, EX_NON_DISCRIMINATING)


def _second_look(outcome: str, expected_output, buggy_output_reported,
                 b_val, f_val) -> dict:
    """Re-judge one undecided row against the report, reading the reported values
    with :func:`_parse_rich` instead of :func:`_parse_expected`.

    Returns ``{"outcome", "how", "b_match", "f_match"}`` — `how` names the reading
    that decided it and is empty when nothing could.
    """
    keep = {"outcome": outcome, "how": "", "b_match": None, "f_match": None}
    e_ok, want = _parse_rich(expected_output)
    b_ok, bug = _parse_rich(buggy_output_reported)
    if outcome == EX_NON_DISCRIMINATING and e_ok and b_ok and _agrees_rich(bug, want):
        return keep                 # the report really does name one value twice
    if e_ok:
        bm, fm = _agrees_rich(b_val, want), _agrees_rich(f_val, want)
        if fm != bm:
            return {"outcome": EX_REPRODUCED if fm else EX_INVERTED,
                    "how": "the reported expected value, read as "
                           f"{type(want).__name__}",
                    "b_match": bm, "f_match": fm}
        if bm and fm:
            return keep             # both revisions answer as the report wants
    if b_ok:
        bm, fm = _agrees_rich(b_val, bug), _agrees_rich(f_val, bug)
        if bm and not fm:
            return {"outcome": EX_REPRODUCED,
                    "how": "the buggy revision produced the reported buggy value",
                    "b_match": keep["b_match"], "f_match": keep["f_match"]}
        if fm and not bm:
            return {"outcome": EX_STILL_WRONG,
                    "how": "the fixed revision still produces the reported buggy value",
                    "b_match": keep["b_match"], "f_match": keep["f_match"]}
    if outcome == EX_DIVERGED and _prose_recovers(expected_output, f_val, b_val):
        return {"outcome": EX_REPRODUCED,
                "how": "the report's prose names a value only the fixed revision produced",
                "b_match": False, "f_match": True}
    return keep


def judge_example(inp: str, expected_output, buggy_output_reported,
                  b_name, b_val, f_name, f_val, evaluated_want=None) -> dict:
    """Judge ONE reported example against both revisions -> the row that lands in
    ``comparison.examples.examples``.

    Split out of :func:`compare_examples` so a finished result can be re-graded
    from its stored rows by exactly the code that graded it the first time
    (``regrade.py``) — the observations are all there, only the reading of them
    changed.
    """
    parsed, want = _parse_expected(expected_output)
    want_source = "literal" if parsed else ""
    if evaluated_want is not None and not _eval_error(evaluated_want):
        parsed, want, want_source = True, evaluated_want, "evaluated"
    # An example that would not compile was never run against the revision.
    # Scoring it as "the buggy side errored" would turn a report written in
    # prose into evidence of a defect.
    if _is_harness_error(b_val):
        b_name, b_val = None, None
    if _is_harness_error(f_val):
        f_name, f_val = None, None
    # an expression the probe could not evaluate was never observed on that
    # revision — scoring the error text as a value would report a broken
    # preamble as a fix that did not work
    b_err = _eval_error(b_val)
    f_err = _eval_error(f_val)
    numeric_want = parsed and isinstance(want, (int, float)) and not isinstance(want, bool)
    if numeric_want and _is_dtype_name(b_val):
        b_err = b_err or "the probe recorded a dtype, not the reported value"
    if numeric_want and _is_dtype_name(f_val):
        f_err = f_err or "the probe recorded a dtype, not the reported value"
    b_ok = b_name is not None and not b_err
    f_ok = f_name is not None and not f_err
    # `_agrees_rich`, not `_agrees`: one expected value against a swept record
    # ("op(td, 1.0) for op in {mul, truediv, floordiv}" -> three answers) is the
    # report's claim about every case, not about the list of them
    b_match = _agrees_rich(b_val, want) if (parsed and b_ok) else None
    f_match = _agrees_rich(f_val, want) if (parsed and f_ok) else None
    b_parsed, b_want = _parse_expected(buggy_output_reported)
    # One side raising while the other answers IS a difference — and for a
    # report whose buggy output is "undefined or error" it is the difference
    # being claimed. Only a failure on BOTH sides means the probe, not the
    # revision, is at fault (a preamble that binds nothing raises everywhere).
    differ = (not _agrees(b_val, f_val)) if (b_ok and f_ok) else (
        True if (b_ok != f_ok and (b_name or f_name)) else None)

    if b_name is None or f_name is None:
        # One side has no record at all — its probe never ran, or recorded
        # nothing. That is a measurement that did not happen, and calling it
        # "inverted" would report the FIX as having caused the defect.
        outcome = EX_NOT_OBSERVED
    elif not b_ok and not f_ok:
        outcome = EX_NOT_OBSERVED
    elif b_ok != f_ok:
        if parsed:
            outcome = (EX_REPRODUCED if (f_ok and f_match) else
                       EX_INVERTED if b_ok else EX_STILL_WRONG)
        else:
            # no literal to check, but the report said buggy fails and it did
            outcome = EX_REPRODUCED if f_ok else EX_INVERTED
    elif parsed:
        outcome = _example_outcome(b_match, f_match)
    elif b_parsed:
        # the expectation is prose but the REPORTED BUGGY value is a literal
        # ("nan"): the claim is still checkable from the other end
        outcome = (EX_REPRODUCED if _agrees(b_val, b_want) and not _agrees(f_val, b_want)
                   else EX_STILL_WRONG if _agrees(f_val, b_want)
                   else EX_SAME_ON_BOTH if not differ else EX_DIVERGED)
    else:
        # neither reported value is a literal, so the revisions' own answers
        # are the only evidence — and a difference is what the report claims
        outcome = EX_DIVERGED if differ else EX_SAME_ON_BOTH
    # the mined buggy value, when it too is a literal, is a second check:
    # not just "buggy differed" but "buggy differed the way the report said"
    if parsed and b_parsed and _agrees(b_want, want):
        outcome = EX_NON_DISCRIMINATING
    # …and when the first pass had nothing to compare, read the reported values
    # the way a reader does: as a string, an exact integer, a printed array, or a
    # sentence naming a value. This only ever decides a row that divergence alone
    # was carrying.
    recovered = ""
    if outcome in _SECOND_LOOK and b_ok and f_ok:
        again = _second_look(outcome, expected_output, buggy_output_reported,
                             b_val, f_val)
        if again["how"]:
            outcome, recovered = again["outcome"], again["how"]
            if again["b_match"] is not None:
                b_match, f_match = again["b_match"], again["f_match"]
                want_source = want_source or "re-read"
    row = {
        "input": inp,
        "expected_output": expected_output,
        "expected_value": _json_safe(want) if parsed else None,
        "expected_from": want_source or "prose",
        "buggy_output_reported": buggy_output_reported,
        "outcome": outcome,
        "revisions_differ": differ,
        "buggy": {"record": b_name, "observed": _json_safe(b_val),
                  "evaluation_error": b_err, "matches_expected": b_match,
                  "matches_reported_buggy": (None if not b_parsed or b_name is None
                                             else _agrees(b_val, b_want))},
        "fixed": {"record": f_name, "observed": _json_safe(f_val),
                  "evaluation_error": f_err, "matches_expected": f_match},
    }
    if recovered:
        row["recovered_by"] = recovered
    return row


def examples_summary(rows: list, evaluable: bool = True) -> dict:
    """Aggregate judged example rows into the examples verdict.

    Shared with the re-grader, so a corrected row set aggregates by the same
    rules the harness applies live.
    """
    skip = (EX_UNJUDGEABLE, EX_NOT_OBSERVED, EX_NON_DISCRIMINATING)
    judged = [r for r in rows if r["outcome"] not in skip]
    n_ok = sum(1 for r in judged if r["outcome"] in (EX_REPRODUCED, EX_DIVERGED))
    # an example that raised on BOTH revisions was not evaluated, and an
    # unevaluated example is an unchecked claim: the values the report names are
    # the issue, so leaving some of them unseen cannot count as reproducing it
    unobserved = [r for r in rows if r["outcome"] == EX_NOT_OBSERVED]
    # An example that both revisions answer as expected is not a failure — it is
    # simply not discriminating, and the mined set is full of them. What counts
    # against the report is an example the fix did NOT correct, or one that only
    # goes wrong on fixed.
    contested = [r for r in judged if r["outcome"] in (EX_STILL_WRONG, EX_INVERTED)]
    if not rows:
        verdict = None                              # the strategy mined no examples
    elif not judged:
        verdict = EX_NOT_OBSERVED
    elif n_ok and not contested and unobserved:
        verdict = EX_INCOMPLETE
    elif n_ok and not contested:
        # "diverged" when the difference is all the reports let us check, so the
        # verdict never claims a literal match that was never possible
        verdict = (EX_REPRODUCED
                   if any(r["outcome"] == EX_REPRODUCED for r in judged)
                   else EX_DIVERGED)
    elif n_ok:
        verdict = "partial"
    else:
        verdict = "not_reproduced"
    return {"n_examples": len(rows), "n_judged": len(judged), "n_reproduced": n_ok,
            "evaluable": evaluable,
            "n_diverged": sum(1 for r in rows if r["outcome"] == EX_DIVERGED),
            "n_recovered": sum(1 for r in rows if r.get("recovered_by")),
            "n_unobserved": len(unobserved), "n_contested": len(contested),
            "unobserved": [r["input"] for r in unobserved][:20],
            "verdict": verdict, "examples": rows}


def compare_examples(buggy: dict, fixed: dict, spec=None) -> dict:
    """Check the mined input/output examples against both revisions.

    Each example names an input, the value the report says the buggy revision
    produced, and the value it should produce. The reproduction is only faithful
    to the report if BOTH sides show up: the buggy revision must not produce the
    expected value, and the fixed one must. Matching is by record name, so a
    probe records an example under the example's own input expression.
    """
    examples = ((spec.strategy or {}).get("input_output_examples") or []
                if spec is not None else [])
    b_rec, f_rec = _revision_records(buggy), _revision_records(fixed)
    # an expectation the revision evaluated for us beats one parsed from prose;
    # the fixed revision's copy is preferred, since that is where it must hold
    evaluated = {**_revision_records(buggy, expected=True),
                 **_revision_records(fixed, expected=True)}
    rows = []
    for ex in examples:
        if not isinstance(ex, dict):
            continue
        inp = str(ex.get("input", ""))[:300]
        _, evaluated_want = _lookup_record(evaluated, inp)
        b_name, b_val = _collapse(_lookup_records(b_rec, inp))
        f_name, f_val = _collapse(_lookup_records(f_rec, inp))
        rows.append(judge_example(inp, ex.get("expected_output"),
                                  ex.get("buggy_output"), b_name, b_val,
                                  f_name, f_val, evaluated_want))

    # Whether this instance's language can evaluate a reported expression at all.
    # Rust, Go, Java and C/C++ cannot, so "not observed" there is a limit of the
    # harness — it must not be read as the values failing to reproduce.
    from .repro_probe_langs import EXAMPLE_LANGUAGES
    evaluable = getattr(spec, "language", "python") in EXAMPLE_LANGUAGES if spec else True
    return examples_summary(rows, evaluable)


def _execution_summary(obs: dict) -> dict:
    cmds = obs.get("commands") or []
    failed = [c for c in cmds if c.get("exit_code") not in (0, None)]
    return {
        "n_commands": len(cmds),
        "n_failed": len(failed),
        "ran_test": obs.get("test") is not None,
        "failed_setup_or_build": [c.get("command", "") for c in failed
                                  if c.get("purpose") in ("setup", "build")][:5],
    }


#: what the value oracle's verdict means as a PAIR outcome. Pattern-matching the
#: examples observation for NaN/Inf asks the wrong question — on a bug whose fix
#: is to produce NaN or inf the correct post-fix value trips `has_inf` and the
#: pair reads `persistent` about a value the examples oracle just confirmed
#: (cvxpy#3267, xarray#11478, numpy#31620). So that one pair takes its outcome
#: from the comparison against the report instead.
_EXAMPLES_AS_PAIR = {EX_REPRODUCED: FLIPPED, EX_DIVERGED: DIVERGED,
                     EX_SAME_ON_BOTH: ABSENT, EX_INVERTED: INVERTED,
                     EX_STILL_WRONG: PERSISTENT, EX_FIX_ONLY: ABSENT}


def _reconcile_examples_pair(numeric: dict, examples: dict) -> dict:
    """Re-judge the examples-derived pair by the value oracle, then re-aggregate.

    Only when that oracle reached a verdict on it: an unjudgeable or unobserved
    examples set says nothing, and the pathology reading is then all there is.
    """
    mapped = _EXAMPLES_AS_PAIR.get((examples or {}).get("verdict"))
    pairs = (numeric or {}).get("pairs") or []
    if mapped is None or not any(p.get("label") == EXAMPLES_PROBE_LABEL for p in pairs):
        return numeric
    for p in pairs:
        if p["label"] == EXAMPLES_PROBE_LABEL:
            p["outcome_from_pathology"] = p["outcome"]      # kept for inspection
            p["outcome"] = mapped
            p["judged_by"] = "value oracle"
    return dict(numeric, verdict=aggregate_pairs(pairs), pairs=pairs)


def compare(buggy: dict, fixed: dict, spec=None) -> dict:
    """Compare the buggy and fixed observations.

    Each side is a bundle ``{"test": <run record>, "probes": {label: observation},
    "commands": [...]}``. Produces the test-status transition, the numerical
    oracle's verdict, and the execution summary the finalizer needs to tell a
    setup failure apart from a genuine negative result.
    """
    b = parse_test_outcome((buggy or {}).get("test"))
    f = parse_test_outcome((fixed or {}).get("test"))
    # A test that HANGS on buggy and passes on fixed is a failure on buggy: the
    # hang is the defect (numpy#27048's zipf never returns for a near 1, sympy#28307
    # never finishes), and the fixed revision proves the same test can complete.
    # Classified as a timeout it read as SETUP_FAILURE; it is FAIL -> PASS. A test
    # that CRASHES on buggy (abort, segfault) and passes on fixed is the same.
    hung, crashed = b.get("status") == "timeout", b.get("failure_kind") == "crash"
    if (hung or crashed) and f.get("status") == "pass":
        b = {**b, "status": "fail", "failure_kind": "timeout" if hung else "crash"}
    examples = compare_examples(buggy or {}, fixed or {}, spec)
    return {
        "buggy": b,
        "fixed": f,
        "transition": f"{b['status']}->{f['status']}",
        "fail_to_pass": b["status"] == "fail" and f["status"] == "pass",
        "numeric": _reconcile_examples_pair(
            compare_probes((buggy or {}).get("probes") or {},
                           (fixed or {}).get("probes") or {}),
            examples),
        "examples": examples,
        "execution": {"buggy": _execution_summary(buggy or {}),
                      "fixed": _execution_summary(fixed or {})},
        "success_criterion": ((spec.strategy or {}).get("success_criterion", "")
                              if spec is not None else ""),
    }


# --------------------------------------------------------------------------- #
# finalize: comparison -> status + evidence
# --------------------------------------------------------------------------- #
def _numeric_note(num_verdict) -> str:
    return {
        FLIPPED: "the numerical oracle agrees (defect signal present on buggy, absent on fixed)",
        PERSISTENT: "the numerical probe still shows the defect on fixed",
        DIVERGED: "no probe is pathological, but the two revisions record different values",
        ABSENT: "no numerical probe exposed the defect on either revision",
        INVERTED: "the numerical probe shows the defect only on fixed",
        "mixed": "the numerical probes disagree with each other",
        "inconclusive": "the numerical probes did not complete on both revisions",
    }.get(num_verdict, "")


def finalize(comparison: dict, spec=None, notes: str = "", probes: bool = True) -> dict:
    """Map a comparison to the final reproduction status + evidence report.

    A SETUP_FAILURE carries `setup_cause` — so does any verdict reached while the
    reproducing test could not run on a revision, since that is the part of a
    PARTIALLY_REPRODUCED still to be fixed.
    """
    report = _finalize(comparison, spec, notes, probes)
    tests = [comparison.get(rev) or {} for rev in ("buggy", "fixed")]
    if report["status"] == SETUP_FAILURE or any(
            t.get("status") in ("error", "timeout") or t.get("failure_kind") == "skipped"
            for t in tests):
        report["setup_cause"] = setup_cause(comparison, spec)
    return report


def _cause(cause: str, detail: str = "") -> dict:
    return {"cause": cause, "detail": (detail or "")[:300]}


def setup_cause(comparison: dict, spec=None) -> dict:
    """WHY a revision could not run the test: ``{"cause", "detail"}``.

    Most specific first. A package the probes could not import explains a
    test error too, so it outranks it; a timeout is its own fix (a budget, not
    a bug); a test error names its first real error line; a failed setup
    command, then nothing having run at all, are what is left.
    """
    comparison = comparison or {}
    package = python_package(spec) if spec is not None else ""
    for pair in (comparison.get("numeric") or {}).get("pairs") or []:
        for rev in ("buggy", "fixed"):
            if pair.get(f"{rev}_status") == "environment_error":
                reasons = (pair.get(rev) or {}).get("reasons") or [""]
                return _cause(CAUSE_PACKAGE_NOT_IMPORTABLE, f"{rev}: {reasons[0]}")
    tests = {rev: comparison.get(rev) or {} for rev in ("buggy", "fixed")}
    if package:
        for rev, t in tests.items():
            line = t.get("error_line") or ""
            if t.get("status") == "error" and f"No module named '{package}'" in line:
                return _cause(CAUSE_PACKAGE_NOT_IMPORTABLE, f"{rev}: {line}")
    for rev, t in tests.items():
        if t.get("status") == "timeout":
            return _cause(CAUSE_TIMEOUT, f"{rev}: the test run timed out")
    for rev, t in tests.items():
        if t.get("failure_kind") == "skipped":
            return _cause(CAUSE_TESTS_SKIPPED, f"{rev}: " + (t.get("skip_reason")
                                                             or "every selected test skipped"))
    for rev, t in tests.items():
        if t.get("status") == "error":
            kind = (CAUSE_TEST_COLLECTION_ERROR if t.get("collection_error")
                    else CAUSE_TEST_ERROR)
            return _cause(kind, f"{rev}: " + (t.get("error_line")
                                              or f"exit code {t.get('exit_code')}"))
    execution = comparison.get("execution") or {}
    for rev in ("buggy", "fixed"):
        failed = (execution.get(rev) or {}).get("failed_setup_or_build") or []
        if failed:
            return _cause(CAUSE_SETUP_COMMANDS_FAILED, f"{rev}: {failed[0]}")
    if not execution or all(not (s or {}).get("n_commands") for s in execution.values()):
        return _cause(CAUSE_NOTHING_RAN, "no command ran on either revision")
    return _cause(CAUSE_UNKNOWN)


def setup_failure_report(cause: str, detail: str, rationale: str) -> dict:
    """A SETUP_FAILURE decided before anything ran — no comparison to derive it from."""
    report = _report(SETUP_FAILURE, rationale)
    report["setup_cause"] = _cause(cause, detail)
    return report


def _finalize(comparison: dict, spec=None, notes: str = "",
              probes: bool = True) -> dict:
    """The status decision itself; :func:`finalize` adds the setup cause."""
    b = comparison["buggy"]["status"]
    f = comparison["fixed"]["status"]
    num = (comparison.get("numeric") or {}).get("verdict")
    ex = comparison.get("execution") or {}
    setup_broken = any((s or {}).get("failed_setup_or_build") for s in ex.values())
    # a probe that could not import the package under test is the environment
    # failing, not the probe — with no test result either, that is a setup
    # failure, not an ambiguous one
    setup_broken = setup_broken or any(
        p.get(f"{rev}_status") == "environment_error"
        for p in (comparison.get("numeric") or {}).get("pairs") or []
        for rev in ("buggy", "fixed"))
    nothing_ran = all(not (s or {}).get("n_commands") for s in ex.values()) if ex else True

    def note(base: str) -> str:
        extra = _numeric_note(num)
        return f"{base}; {extra}" if extra else base

    # 1. no test result on either revision — the numerical oracle decides
    if b == "missing" and f == "missing":
        if num == FLIPPED:
            status, why = REPRODUCED, ("no test run recorded, but the numerical probe shows "
                                       "the defect on buggy and not on fixed")
        elif num == DIVERGED:
            status, why = PARTIALLY_REPRODUCED, ("no test run recorded, but the probes record "
                                                 "different values on the two revisions")
        elif num in (PERSISTENT, ABSENT):
            status, why = NOT_REPRODUCED, note("no test run recorded and the numerical probe "
                                               "does not separate the revisions")
        elif num in (INVERTED, "mixed"):
            status, why = AMBIGUOUS_RESULT, note("no test run recorded")
        elif setup_broken or nothing_ran:
            status, why = SETUP_FAILURE, ("neither revision produced a test or probe result "
                                          "(setup/build never succeeded)")
        else:
            status, why = AMBIGUOUS_RESULT, "nothing was executed that could decide the outcome"
        return _report(status, why, comparison, notes)

    # 2. the test oracle failed on ONE revision, but a paired probe shows the
    # defect on exactly one. That numerical evidence is credited — short of a
    # full reproduction, since the test never completed a FAIL→PASS.
    #
    # Two things it deliberately does NOT credit. A test that ran on NEITHER
    # revision (error→error: pandas' pytest refusing to load a plugin) means the
    # benchmark's primary oracle never executed, and whatever broke it is as
    # likely to have shaped the probes; that is a setup failure, not a partial
    # reproduction. And DIVERGED only says the values differ — it does not say
    # the difference is the reported defect — so it is never enough on its own.
    unusable_test = {"error", "timeout", "no_tests"}
    ran_somewhere = not (b in unusable_test and f in unusable_test)
    if num == FLIPPED and ran_somewhere and (b in unusable_test or f in unusable_test):
        return _report(PARTIALLY_REPRODUCED, comparison=comparison, notes=notes,
                       rationale=f"the reproducing test did not run cleanly on one revision "
                                 f"(buggy={b}, fixed={f}), but the numerical probe shows the "
                                 f"defect only on buggy")

    # 3. the test could not run cleanly on a revision
    skipped = [rev for rev in ("buggy", "fixed")
               if comparison[rev].get("failure_kind") == "skipped"]
    if skipped:
        # Every selected test SKIPPED: the selector found the test, and the
        # test itself declined to run here — a GPU it does not have, a TPU, a
        # platform. That is the environment, not a stale selector.
        reason = comparison[skipped[0]].get("skip_reason") or "no reason given"
        return _report(SETUP_FAILURE, comparison=comparison, notes=notes,
                       rationale=f"every selected test was SKIPPED on "
                                 f"{' and '.join(skipped)} ({reason})")
    if b == "no_tests" or f == "no_tests":
        return _report(INVALID_STRATEGY, comparison=comparison, notes=notes,
                       rationale="the test command selected no tests (stale selector?)")
    if b in ("error", "timeout") or f in ("error", "timeout"):
        # the probe evidence is kept in the rationale — it is real, it just cannot
        # stand in for a test that never ran
        return _report(SETUP_FAILURE, comparison=comparison, notes=notes,
                       rationale=note(f"a revision did not run the test cleanly "
                                      f"(buggy={b}, fixed={f})"))
    if b == "missing" or f == "missing":
        if num == FLIPPED:
            return _report(PARTIALLY_REPRODUCED, comparison=comparison, notes=notes,
                           rationale="the test ran on only one revision, but the numerical "
                                     "probe separates them")
        return _report(AMBIGUOUS_RESULT, comparison=comparison, notes=notes,
                       rationale=f"the reproducing test ran on only one revision "
                                 f"(buggy={b}, fixed={f})")

    # 4. both revisions produced a test result
    if b == "fail" and f == "pass":
        # The test flipping is the transition. The reported VALUES are the issue:
        # a benchmark instance claims specific outputs on each revision, and an
        # agent can land FAIL→PASS without ever showing them. So the full status
        # requires both; the test alone earns the partial one, saying which half
        # is missing.
        ex = comparison.get("examples") or {}
        ev, base = ex.get("verdict"), "reproducing test FAILS on buggy and PASSES on fixed"
        if not probes:
            # --no-probes: no numerical probe ran and the report's values were
            # never evaluated, so there is no half missing to report — the test
            # oracle answered, alone, and the status says exactly that.
            return _report(F2P_REPRODUCED, comparison=comparison, notes=notes,
                           rationale=f"{base}; numerical probes are disabled "
                                     f"(--no-probes), so the test transition is "
                                     f"the whole of the evidence")
        if comparison["buggy"].get("failure_kind") == "sanitizer":
            base += (" (UBSan, in the code the fix changed: "
                     f"{(comparison['buggy'].get('sanitizer_reports') or ['?'])[0]})")
        # A test that does not BUILD on the buggy revision transitions, but it
        # never produced a number: nothing was measured, so nothing shows the
        # defect's numerical character. That is a partial reproduction unless
        # the probes or the reported examples supply the missing half.
        if (comparison["buggy"].get("failure_kind") == "build"
                and num not in (FLIPPED, DIVERGED)
                and ev not in (EX_REPRODUCED, EX_DIVERGED)):
            base = ("the reproducing test does not BUILD on buggy and PASSES on fixed "
                    "(the buggy revision cannot compile the test target)")
            return _report(PARTIALLY_REPRODUCED,
                           note(f"{base} — a real transition, but no numerical "
                                f"difference was observed"), comparison, notes)
        counts = f"{ex.get('n_reproduced')}/{ex.get('n_judged')} reported example(s)"
        if ev is None:                       # the strategy named no examples
            return _report(REPRODUCED, note(base), comparison, notes)
        if ev in (EX_REPRODUCED, EX_DIVERGED):
            return _report(REPRODUCED, note(f"{base}, and {counts} show the reported "
                                            f"difference between the revisions"),
                           comparison, notes)
        if ev == EX_INCOMPLETE:
            return _report(PARTIALLY_REPRODUCED,
                           f"{base}, and {counts} reproduce, but "
                           f"{ex.get('n_unobserved')} of {ex.get('n_examples')} reported "
                           f"example(s) could not be evaluated on either revision, so those "
                           f"values remain unchecked", comparison, notes)
        if ev == "partial":
            return _report(PARTIALLY_REPRODUCED,
                           f"{base}, but only {counts} reproduce — "
                           f"{ex.get('n_contested')} still disagree on fixed", comparison, notes)
        if ev == EX_NOT_OBSERVED:
            if not ex.get("evaluable", True):
                # the language cannot evaluate a reported expression, so nothing
                # was withheld — the test transition is all this instance can give
                return _report(REPRODUCED,
                               f"{base}; the reported values could not be checked "
                               f"automatically in this language", comparison, notes)
            return _report(PARTIALLY_REPRODUCED,
                           f"{base}, but the reported input/output examples were never "
                           f"observed on both revisions, so the values themselves are "
                           f"unverified", comparison, notes)
        return _report(PARTIALLY_REPRODUCED,
                       f"{base}, but the reported values do not separate the revisions "
                       f"({counts})", comparison, notes)
    if b == "fail" and f == "fail":
        bf = comparison["buggy"].get("failed") or 0
        ff = comparison["fixed"].get("failed") or 0
        if num == FLIPPED:
            return _report(PARTIALLY_REPRODUCED,
                           "the test still fails on fixed, but the numerical probe shows the "
                           "defect only on buggy", comparison, notes)
        if ff < bf:
            return _report(PARTIALLY_REPRODUCED,
                           f"fixed reduces failures ({bf}->{ff}) but does not fully pass",
                           comparison, notes)
        return _report(NOT_REPRODUCED, note("test fails on BOTH revisions (the fix did not flip it)"),
                       comparison, notes)
    if b == "pass" and f == "pass":
        if num == FLIPPED:
            return _report(PARTIALLY_REPRODUCED,
                           "the test passes on both revisions, but the numerical probe shows "
                           "the defect only on buggy", comparison, notes)
        return _report(NOT_REPRODUCED, note("test passes on BOTH revisions (it does not expose the bug)"),
                       comparison, notes)
    if b == "pass" and f == "fail":
        return _report(AMBIGUOUS_RESULT, note("test PASSES on buggy but FAILS on fixed (inverted)"),
                       comparison, notes)
    return _report(AMBIGUOUS_RESULT, f"unclassified transition {comparison['transition']}",
                   comparison, notes)


def _report(status: str, rationale: str = "", comparison: dict | None = None,
            notes: str = "") -> dict:
    """The evidence report: the status, why, and everything it was derived from."""
    comparison = comparison or {}
    numeric = comparison.get("numeric") or {}
    examples = comparison.get("examples") or {}
    return {
        "status": status,
        "rationale": rationale,
        "transition": comparison.get("transition", ""),
        "fail_to_pass": comparison.get("fail_to_pass"),
        "numeric_verdict": numeric.get("verdict"),
        "examples_verdict": examples.get("verdict"),
        "success_criterion": comparison.get("success_criterion", ""),
        "agent_notes": notes,
        "evidence": {
            "test": {"buggy": comparison.get("buggy"), "fixed": comparison.get("fixed")},
            "probes": numeric.get("pairs", []),
            "examples": examples.get("examples", []),
            "execution": comparison.get("execution", {}),
        },
    }

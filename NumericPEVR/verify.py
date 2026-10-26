from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

SETUP_SIGNATURES = (
    (re.compile(r"\bModuleNotFoundError\b|\bImportError\b", re.I), "import_error"),
    (re.compile(r"\berror: (?:command|subprocess-exited|metadata-generation)\b"
                r"|\bfailed building wheel\b|\bninja: build stopped\b"
                r"|\bgcc\b.*\berror:|\bfatal error:.*\.h: No such file", re.I),
     "build_failure"),
    (re.compile(r"ERROR collecting|INTERNALERROR|conftest\.py.*error|"
                r"fixture '.*' not found|\bno tests ran\b"
                r"|^\s*collected 0 items(?:\s*/\s*\d+ errors?)?\s*$", re.I | re.M),
     "collection_failure"),
    (re.compile(r"\bPermissionError\b|\bOSError\b.*\b(?:No space|Read-only)\b", re.I),
     "environment_error"),
    (re.compile(r"has not been built correctly|__check_build"
                r"|please reinstall .* package|cannot import name .* from "
                r"partially initialized module", re.I),
     "build_failure"),
)

NUMERICAL_SIGNATURES = (
    (re.compile(r"\bassert_allclose\b|\bassert_array_almost_equal\b|"
                r"\bMismatched elements\b|\bMax absolute difference\b|"
                r"\bMax relative difference\b|\bNot equal to tolerance\b", re.I),
     "numerical_assertion"),
    (re.compile(r"\bFloatingPointError\b|\bOverflowError\b|\bZeroDivisionError\b|"
                r"\bOutOfBounds\w*\b|invalid value encountered|divide by zero|"
                r"overflow encountered|underflow encountered", re.I),
     "numerical_exception"),
    (re.compile(r"\bnan\b|\binf\b|-inf", re.I), "special_value_in_output"),
    (re.compile(r"^E\s+assert\b|^E\s+AssertionError", re.M), "assertion"),
    (re.compile(r"\b(?:RuntimeError|ArithmeticError|ValueError|RuntimeWarning)\b"
                r"[^\n]*\b(?:converge\w*|did not converge|iterations?|tolerance|"
                r"ill[- ]conditioned|singular|step size|line search)\b", re.I),
     "convergence_failure"),
    (re.compile(r"\b\w*(?:Convergence|LineSearch|Optimize|Integration|Precision)"
                r"Warning\b", re.I), "convergence_failure"),
    (re.compile(r"^E\s+Failed:[^\n]*\b(?:approximately equal|not equal|"
                r"almost equal|differ|mismatch\w*|tolerance|exceed\w*)\b",
                re.I | re.M), "numerical_assertion"),
)

_ACTUAL_DESIRED = re.compile(r"ACTUAL:\s*(.+?)\s*$.*?^.*?DESIRED:\s*(.+?)\s*$",
                             re.M | re.S)
_ASSERT_PAIR = re.compile(r"assert\s+(\S+)\s*[=!<>]=\s*(\S+)")
_MAX_ABS = re.compile(r"Max absolute difference:?\s*([-\d.eE+]+)")

_WRAPPED = re.compile(r"^(?:[\w.]*(?:array|float\d*|int\d*|tensor|Decimal))\((.*)\)$")


_RAISED = re.compile(r"^E\s+(?:\w+\.)*\w*(?:Error|Exception)\b", re.M)


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def _reported_exception(output: str, context) -> bool:
    want = ((getattr(context, "numerical_failure", {}) or {}) or {})
    want = want.get("manifestation") if isinstance(want, dict) else None
    if (want or "").lower() != "exception":
        return False
    return bool(_RAISED.search(strip_ansi(output)))


def failure_type(output: str) -> str:
    text = strip_ansi(output)
    for pattern, name in SETUP_SIGNATURES:
        if pattern.search(text):
            return name
    for pattern, name in NUMERICAL_SIGNATURES:
        if pattern.search(text):
            return name
    return "unknown_failure"


def observed_expected(output: str) -> dict:
    text = output or ""
    values: dict = {}
    text = strip_ansi(text)
    m = _ACTUAL_DESIRED.search(text)
    numeric_only = False
    if not m:
        m, numeric_only = _ASSERT_PAIR.search(text), True
    if m:
        actual, desired = _scalar(m.group(1)), _scalar(m.group(2))
        both_known = actual is not None and desired is not None
        if both_known and numeric_only and not (
                isinstance(actual, float) and isinstance(desired, float)):
            both_known = False
        if both_known:
            values["observed_value"] = actual
            values["expected_value"] = desired
    diff = _MAX_ABS.search(text)
    if diff:
        values["max_abs_difference"] = _num(diff.group(1))
    return values


def _scalar(text: str):
    raw = (text or "").strip().strip(",").strip()
    while True:
        m = _WRAPPED.match(raw)
        if not m:
            break
        raw = m.group(1).strip()
    if not raw or raw in ("=", "==", "<", ">"):
        return None
    return _num(raw)


def _num(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return text


BUG_EXPOSING = ("numerical_assertion", "numerical_exception", "assertion",
                "special_value_in_output", "convergence_failure",
                "reported_exception")
SETUP_KINDS = ("import_error", "build_failure", "collection_failure",
               "environment_error")

F2P, P2P, F2F, P2F = "F2P", "P2P", "F2F", "P2F"
SETUP_ERROR, INVALID = "SETUP_ERROR", "INVALID"


@dataclass
class SideReport:
    execution: str = "not_run"
    test_outcome: str = "UNKNOWN"
    failure_type: str = ""
    values: dict = field(default_factory=dict)
    exit_code: int | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {})}


@dataclass
class Verdict:
    reproduction: str
    buggy: SideReport
    fixed: SideReport
    reason: str = ""
    valid: bool = False

    def as_dict(self) -> dict:
        return {"buggy": self.buggy.as_dict(), "fixed": self.fixed.as_dict(),
                "reproduction": self.reproduction, "valid": self.valid,
                "reason": self.reason}


_NO_TESTS = re.compile(r"^\s*collected 0 items(?:\s*/\s*\d+ errors?)?\s*$"
                       r"|\bno tests ran\b", re.I | re.M)
_RAN_SOMETHING = re.compile(
    r"\b\d+\s+(?:passed|failed|xfailed|xpassed|errors?|skipped)\b", re.I)


def _collected_nothing(output: str) -> bool:
    text = output or ""
    if _RAN_SOMETHING.search(text):
        return False
    return bool(_NO_TESTS.search(text))


def read_side(record: dict | None) -> SideReport:
    if not record:
        return SideReport()
    output = strip_ansi((record.get("output") or "") + (record.get("stderr") or ""))
    code = record.get("exit_code")
    if record.get("timed_out"):
        return SideReport("failed_to_run", "ERROR", "timeout", {}, code)
    kind = failure_type(output) if code not in (0, None) else ""
    if _collected_nothing(output):
        return SideReport("completed", "NO_TESTS", "collection_failure", {}, code)
    if code == 0:
        return SideReport("completed", "PASS", "", {}, code)
    execution = "failed_to_run" if kind in SETUP_KINDS else "completed"
    outcome = "ERROR" if kind in SETUP_KINDS else "FAIL"
    return SideReport(execution, outcome, kind, observed_expected(output), code)


def classify(buggy: dict | None, fixed: dict | None, *,
             hang_expected: bool = False, context=None) -> Verdict:
    b, f = read_side(buggy), read_side(fixed)

    if b.failure_type == "unknown_failure" and context is not None:
        text = ((buggy or {}).get("output") or "") + ((buggy or {}).get("stderr") or "")
        if _reported_exception(text, context):
            b.failure_type = "reported_exception"

    if b.failure_type == "timeout" and f.test_outcome == "PASS":
        return Verdict(F2P, b, f,
                       "buggy does not terminate (timeout) where fixed passes — "
                       + ("the reported manifestation is a hang" if hang_expected
                          else "the hang is the defect"), valid=True)

    if b.test_outcome == "UNKNOWN" or f.test_outcome == "UNKNOWN":
        return Verdict(SETUP_ERROR, b, f, "the test did not run on one revision")
    if "NO_TESTS" in (b.test_outcome, f.test_outcome):
        return Verdict(SETUP_ERROR, b, f, "the selector matched no tests")
    if b.execution == "failed_to_run" or f.execution == "failed_to_run":
        side = "buggy" if b.execution == "failed_to_run" else "fixed"
        kind = b.failure_type if side == "buggy" else f.failure_type
        return Verdict(SETUP_ERROR, b, f, f"{side}: {kind} — the environment, not the bug")

    if b.test_outcome == "FAIL" and f.test_outcome == "PASS":
        if b.failure_type in BUG_EXPOSING:
            return Verdict(F2P, b, f, f"buggy fails with {b.failure_type}", valid=True)
        return Verdict(INVALID, b, f,
                       f"transition looks like F2P but the buggy failure is "
                       f"{b.failure_type or 'untyped'}, which the targeted bug "
                       f"does not produce")
    if b.test_outcome == "PASS" and f.test_outcome == "PASS":
        return Verdict(P2P, b, f, "the bug is not exposed under these conditions")
    if b.test_outcome == "FAIL" and f.test_outcome == "FAIL":
        return Verdict(F2F, b, f, "both revisions fail — shared setup or test problem, "
                                  "or the fix does not address what the test checks")
    if b.test_outcome == "PASS" and f.test_outcome == "FAIL":
        return Verdict(P2F, b, f, "inverted: the fix breaks what the buggy revision passes")
    return Verdict(INVALID, b, f, "unclassified outcome pair")


def matches_reported(verdict: Verdict, context) -> tuple:
    want = ((getattr(context, "numerical_failure", {}) or {}).get("manifestation")
            or "").lower()
    got = verdict.buggy.failure_type
    if not want or want == "unknown" or not got:
        return True, ""
    expected = {
        "wrong_value": ("numerical_assertion", "assertion", "special_value_in_output"),
        "exception": ("numerical_exception",),
        "warning": ("numerical_assertion", "assertion"),
        "hang": ("timeout",),
        "crash": ("environment_error", "numerical_exception"),
    }.get(want, ())
    if expected and got not in expected:
        return False, (f"the report describes a {want}, but the buggy side failed "
                       f"with {got} — the failure may be unrelated to the bug")
    return True, ""


_COMPARISON = re.compile(
    r"^(?:E\s+\S.*"
    r"|\s*(?:Mismatched elements|Max absolute difference|Max relative difference"
    r"|ACTUAL|DESIRED|Not equal to tolerance)\b.*"
    r"|\s*[xy]:\s.*)$", re.M)


def failure_excerpt(output: str, max_lines: int = 12, width: int = 200) -> str:
    lines = [m.group(0).rstrip()[:width]
             for m in _COMPARISON.finditer(strip_ansi(output))]
    seen, keep = set(), []
    for line in lines:
        if line.strip() and line not in seen:
            seen.add(line)
            keep.append(line)
    if len(keep) > max_lines:
        keep = keep[:max_lines] + [f"... ({len(keep) - max_lines} more)"]
    return "\n".join(keep)


_NUMBERS = re.compile(r"-?(?:\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?"
                      r"|\d+[eE][-+]?\d+|\d+)")


def numbers_in(text: str, limit: int = 8) -> list:
    out = []
    for m in _NUMBERS.finditer(text or ""):
        try:
            out.append(float(m.group(0)))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def tolerance_window(buggy_text: str, fixed_text: str) -> dict:
    b, f = numbers_in(buggy_text), numbers_in(fixed_text)
    if not b or not f:
        return {}
    for bv, fv in zip(b, f):
        gap = abs(bv - fv)
        if gap == 0:
            continue
        rel = gap / abs(fv) if fv else None
        return {"buggy_value": bv, "fixed_value": fv, "absolute_gap": gap,
                "relative_gap": rel,
                "atol_must_be_below": gap,
                "suggested_atol": gap / 10.0,
                "suggested_rtol": (rel / 10.0) if rel else None}
    return {}

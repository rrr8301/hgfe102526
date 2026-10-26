from __future__ import annotations

import re
from dataclasses import dataclass

from . import boundaries as B
from . import verify as V


@dataclass(frozen=True)
class Action:
    diagnosis: str
    instruction: str
    evidence_need: str = ""
    forbids: tuple = ()


POLICY: dict[str, Action] = {
    "missing_dependency": Action(
        "missing_dependency",
        "Install the missing dependency, choosing the version the evidence "
        "documents; if none is documented, use the era ceiling from the context "
        "and record that you inferred it.",
        "documented dependency versions and the era ceiling for this package"),
    "version_incompatibility": Action(
        "version_incompatibility",
        "Consult the era-aware dependency evidence in the context and pin to a "
        "version that existed at the PR date and satisfies the documented "
        "constraints. Change one pin at a time and record the attempt.",
        "historical version constraints compatible with the PR date",
        forbids=("trying newer versions at random",)),
    "native_library_failure": Action(
        "native_library_failure",
        "Investigate the compiler, BLAS/LAPACK or build configuration named in "
        "the build output; adjust the build, not the test.",
        "build configuration and numerical backend for this revision"),
    "candidate_not_importable": Action(
        "candidate_not_importable",
        "Your generated test could not be imported. The import path is wrong, "
        "not the arithmetic: use the package's PUBLIC API (e.g. `import mpmath; "
        "mpmath.jtheta(...)`) rather than guessing an internal module path, and "
        "check the symbol exists before asserting on its output.",
        "the public API path of the affected function",
        forbids=("guessing internal module paths",)),
    "package_not_built": Action(
        "package_not_built",
        "The package under test is NOT BUILT: it cannot be imported, so every "
        "test errors on BOTH revisions and no test outcome means anything yet. "
        "This is not a missing dependency and not a collection problem — do not "
        "install anything else and do not change the selector. Issue a `rebuild` "
        "action for EACH revision (buggy and fixed) to compile it into the venv "
        "already prepared for it. A rebuild action is only executed when it "
        "carries a `python_version` or `pins`, so give it the interpreter the "
        "revision expects (or the one it already runs) — a bare rebuild is "
        "silently dropped. Run the test only once both revisions import.",
        "the build/install commands this project documents for this revision",
        forbids=("installing further dependencies instead of building",
                 "changing the test selector", "rewriting the test")),
    "collection_failure": Action(
        "collection_failure",
        "Fix how the test is addressed or collected — the selector, the module "
        "path, a missing conftest dependency. Do not rewrite the test.",
        "how this project's tests are addressed and run at this revision",
        forbids=("deselecting the failing test", "rewriting the test")),
    "bug_not_exposed": Action(
        "bug_not_exposed",
        "The buggy revision behaves correctly under these conditions. Check "
        "whether the trigger condition is actually met: the dtype, the magnitude, "
        "the platform-dependent path, the dependency whose version decides it.",
        "what determines whether this behaviour appears — dtype defaults, "
        "dependency versions, build flags, architecture",
        forbids=("loosening the assertion so the buggy side fails",)),
    "candidate_malformed": Action(
        "candidate_malformed",
        "Your candidate was refused before it ran, because it could not have "
        "produced a meaningful verdict. Fix exactly what the reason says and "
        "change nothing else — the input and the overall structure were fine.",
        forbids=("exact equality against a float",
                 "leaving a placeholder in the test")),
    "candidate_not_discriminating": Action(
        "candidate_not_discriminating",
        "Your test PASSES on the buggy revision, so the INPUT you chose never "
        "reaches the defect. Change the input, not the assertion — a test that "
        "fails on buggy because the oracle was loosened proves nothing. Use the "
        "values the report names VERBATIM: the exact magnitude, dtype, shape and "
        "keyword arguments, not a rounded or simplified stand-in (1e-30 is not "
        "1e-3; float32 is not the float64 default). If the context carries an "
        "input/output example, use that input unchanged. If it names no input, "
        "ask for one rather than guessing a second time — and say in your "
        "assumptions which input you used and where it came from.",
        "the exact input that triggers the reported behaviour: concrete values "
        "with dtype, shape and keyword arguments, and the output each revision "
        "produces for it",
        forbids=("loosening the assertion so the buggy side fails",
                 "asserting that the defect is present",
                 "simplifying or rounding the reported input")),
    "both_fail": Action(
        "both_fail",
        "Both revisions fail. Compare the two failures: if they are the same "
        "error, it is setup or the test, not the bug. Fix the shared cause before "
        "reading anything into the transition.",
        "what the reported failure looks like, so a shared error can be told "
        "apart from the bug"),
    "inverted": Action(
        "inverted",
        "The fixed revision fails where the buggy one passes, so your test "
        "asserts the BUGGY behaviour. Find the assertion that only holds on the "
        "buggy revision — typically one added to 'confirm the failure mode is "
        "present', such as a check that a magnitude is huge or that a value is "
        "wrong — and DELETE it. Keep only assertions stating the correct result. "
        "Do not swap the revisions and do not loosen the oracle.",
        "the expected (correct) value, so the assertion can state it",
        forbids=("asserting the buggy value", "swapping the revisions")),
    "candidate_fails_on_fixed": Action(
        "candidate_fails_on_fixed",
        "Your test fails on the FIXED revision too, so the fault is in your "
        "ORACLE, not in the buggy code. One of three things is true: (a) the "
        "expected value you asserted is not what correct code produces — check "
        "it against the reference in the evidence, and read the actual value in "
        "the failure output of the FIXED run; (b) your tolerance is tighter than "
        "the correct implementation's own accuracy; or (c) you are testing an "
        "input the fix never changed, so both revisions fail for an unrelated "
        "reason — pick the input the report itself names. Do NOT loosen the "
        "tolerance until it passes on both; that produces a test that proves "
        "nothing. Keep the test DETERMINISTIC and small: no random sampling, no "
        "statistical means over millions of draws, no timing.",
        "the exact expected value and the input the report names",
        forbids=("loosening the tolerance until both revisions pass",
                 "random sampling or statistical comparisons",
                 "asserting on timing")),
    "numerical_mismatch": Action(
        "numerical_mismatch",
        "The values differ but not as reported. Inspect dtype, precision and the "
        "trigger evidence before touching the assertion; the expected value may "
        "be right and the input wrong.",
        "the dtype, precision and tolerance this discrepancy depends on",
        forbids=("widening the tolerance to make the assertion pass",)),
    "oracle_incorrect": Action(
        "oracle_incorrect",
        "The generated test's expected value may be wrong. Revise it from the "
        "evidence — the reported expected output, a mathematical property, or a "
        "reference implementation — and say which you used.",
        "the exact expected value or property the report states",
        forbids=("replacing the oracle with whatever the fixed revision prints",)),
    "invalid_failure": Action(
        "invalid_failure",
        "The transition looks like F2P but the buggy failure is of the wrong "
        "kind. Make the test fail for the reported numerical reason, or explain "
        "why the current failure is the bug.",
        "what the reported failure looks like on the buggy revision"),
    "no_diagnostic_evidence": Action(
        "no_diagnostic_evidence",
        "The execution output does not say why this failed. Request targeted "
        "evidence before changing anything else.",
        "whatever the current failure leaves unexplained"),
    "no_candidate_written": Action(
        "no_candidate_written",
        "You have not written T_gen yet, so there was no test to run and "
        "nothing to grade — this is NOT a setup failure and the environment is "
        "fine. Measuring first is reasonable, but a measurement iteration that "
        "ends without a test file cannot produce a verdict, and the run has a "
        "hard iteration cap. Write the candidate test NOW, in this iteration, "
        "as a file write. If your probe did not give you the discriminating "
        "input, write the test against your best current hypothesis and let "
        "the verdict correct it: a P2P tells you the input missed the defect, "
        "which is information, whereas another probe-only iteration tells you "
        "nothing. One more thing to check: a probe is a SINGLE python -c, so "
        "one unexpected exception discards everything after it — wrap each "
        "name in its own try/except and never call inspect.signature on a "
        "name that may be a module-level CONSTANT rather than a function.",
        forbids=("ending an iteration without writing the candidate test",)),
}

_HINTS = (
    ("no module named", "missing_dependency"),
    ("could not find a version", "version_incompatibility"),
    ("requires a different python", "version_incompatibility"),
    ("incompatible", "version_incompatibility"),
    ("undefined symbol", "native_library_failure"),
    ("lapack", "native_library_failure"),
    ("blas", "native_library_failure"),
    ("numpy.dtype size changed", "version_incompatibility"),
    ("error: command", "native_library_failure"),
)


_REMOVED_API = re.compile(r"module '([A-Za-z_][\w.]*)' has no attribute '(\w+)'")


def removed_api_action(dependency: str, attr: str, pinned: str = "") -> Action:
    m = re.match(r"(\d+)\.(\d+)", pinned or "")
    if m:
        bound = f"<{m.group(1)}.{m.group(2)}"
        target = (f"the newest {dependency} below {m.group(1)}.{m.group(2)} as of the "
                  f"commit's date (the previous minor series)")
    else:
        bound, target = "<CURRENT_MINOR", "the previous minor series of the installed version"
    return Action(
        "removed_dependency_api",
        f"{dependency} {pinned or '(the installed release)'} no longer has `{attr}`, and "
        f"this revision still uses it, so the shared failure is the dependency's version, "
        f"not the bug. Rebuild BOTH revisions with pins [\"{dependency}{bound}\"] — {target} "
        f"— as one action: {{\"purpose\": \"rebuild\", \"revision\": \"both\", "
        f"\"pins\": [\"{dependency}{bound}\"]}}. Step down ONE minor version only; go lower "
        f"only if the same attribute is still missing afterwards. Do not `pip install` "
        f"{dependency} yourself: the era constraints file rejects it, and swapping it under a "
        f"compiled package breaks that package's extensions.",
        forbids=(f"jumping to an older {dependency} major (e.g. 1.x) before trying the previous minor",
                 f"pip installing {dependency} over the prepared build"))


def _package_absent(low: str, package: str) -> bool:
    name = (package or "").strip().lower()
    if not name:
        return False
    return (f"no module named '{name}'" in low
            or f"no module named '{name}." in low
            or f"partially initialized module '{name}'" in low)


def diagnose(verdict: V.Verdict, *, task: str, output: str = "",
             context=None, pins: dict | None = None,
             compile_deferred: bool = False, package: str = "") -> Action:
    low = (output or "").lower()
    removed = _REMOVED_API.search(output or "")
    if task == B.HBEC and removed and verdict.reproduction in (V.F2F, V.SETUP_ERROR):
        dependency = removed.group(1).split(".")[0].lower()
        subject = str(getattr(context, "repo", "") or "").rsplit("/", 1)[-1].lower()
        import sys as _sys
        if (dependency != subject and dependency
                and dependency not in getattr(_sys, "stdlib_module_names", ())):
            return removed_api_action(dependency, removed.group(2), (pins or {}).get(dependency, ""))
    if task == B.HBEC and verdict.reproduction in (V.F2F, V.P2P, V.SETUP_ERROR) \
            and (compile_deferred or _package_absent(low, package)):
        return POLICY["package_not_built"]
    if verdict.reproduction == V.SETUP_ERROR:
        if task == B.BRTG and ("no module named" in low or "importerror" in low) \
                and ("test_" in low or "collecting" in low):
            return POLICY["candidate_not_importable"]
        for phrase, name in _HINTS:
            if phrase in low:
                return POLICY[name]
        kind = verdict.buggy.failure_type or verdict.fixed.failure_type
        if kind == "collection_failure":
            return POLICY["collection_failure"]
        if kind == "import_error":
            return POLICY["missing_dependency"]
        if kind == "build_failure":
            return POLICY["native_library_failure"]
        return POLICY["no_diagnostic_evidence"]
    if verdict.reproduction == V.P2P:
        if task == B.BRTG:
            return POLICY["candidate_not_discriminating"]
        return POLICY["bug_not_exposed"]
    if verdict.reproduction == V.F2F:
        if task == B.BRTG:
            return POLICY["candidate_fails_on_fixed"]
        return POLICY["both_fail"]
    if verdict.reproduction == V.P2F:
        return POLICY["inverted"]
    if verdict.reproduction == V.INVALID:
        return POLICY["invalid_failure"]
    if verdict.reproduction == V.F2P:
        ok, note = V.matches_reported(verdict, context)
        if not ok:
            return POLICY["numerical_mismatch"]
        if task == B.BRTG and verdict.buggy.values:
            return POLICY["numerical_mismatch"]
    return POLICY["no_diagnostic_evidence"]


def allowed_moves(task: str, action: Action) -> dict:
    boundary = B.BOUNDARIES[task]
    return {"may_change": boundary.search_variable,
            "must_not_change": boundary.fixed,
            "allowed": list(boundary.allowed),
            "forbidden": list(boundary.forbidden) + list(action.forbids)}


def _gap_from_sides(verdict: V.Verdict) -> dict:
    b = (verdict.buggy.values or {}).get("observed_value")
    f = (verdict.fixed.values or {}).get("observed_value")
    if not isinstance(b, float) or not isinstance(f, float):
        return {}
    gap = abs(b - f)
    if gap == 0:
        return {}
    rel = gap / abs(f) if f else None
    return {"buggy_value": b, "fixed_value": f, "absolute_gap": gap,
            "relative_gap": rel, "atol_must_be_below": gap,
            "suggested_atol": gap / 10.0,
            "suggested_rtol": (rel / 10.0) if rel else None}


def _reading(verdict: V.Verdict, task: str, window: dict | None = None) -> str:
    fixed, buggy = verdict.fixed.values or {}, verdict.buggy.values or {}
    if task != B.BRTG:
        return ("These are the two revisions' own outputs for the developer's "
                "test. Use them to tell a shared setup failure apart from the "
                "bug; the test itself is not yours to change.")
    if verdict.reproduction == V.F2F and window:
        return (f"The two revisions DO differ for this input — by "
                f"{window['absolute_gap']:.6g} — so the input is fine and only "
                f"the oracle is wrong. Assert the fixed value "
                f"{window['fixed_value']!r} with atol="
                f"{window['suggested_atol']:.3g}: that is loose enough to pass "
                f"on fixed and tight enough to fail on buggy "
                f"({window['buggy_value']!r}). Do not use exact equality and do "
                f"not widen beyond {window['absolute_gap']:.6g}.")
    if verdict.reproduction == V.F2F:
        got, want = fixed.get("observed_value"), fixed.get("expected_value")
        if isinstance(got, float) and want is not None:
            return (f"The FIXED revision is correct BY DEFINITION, so {got!r} is "
                    f"the reference value for this input and the {want!r} your "
                    f"test asserted is wrong. Keep the input and the test "
                    f"structure; replace the expected value with {got!r}, at a "
                    f"tolerance {got!r} satisfies. Then check the buggy side's "
                    f"own value below is OUTSIDE that tolerance — if it is not, "
                    f"this input cannot discriminate and the input must change "
                    f"instead.")
        return ("Your test failed on the FIXED revision, so the oracle is wrong, "
                "not the code. Your previous run did not print the correct value "
                "in a form that can be read back, so STOP INFERRING IT: make "
                "your next plan MEASURE it. Address an action to the FIXED "
                "revision whose command is "
                "`cd /tmp && python -c \"import ...; print(repr(...))\"` — "
                "computing your input and printing the result at full "
                "precision. The `cd /tmp` is required: run it inside the "
                "checkout and the import finds the unbuilt source tree instead "
                "of the built package. Read the number off that output and only "
                "then write the assertion with that number in it. Run the same "
                "one-liner against the buggy revision and check the two differ "
                "by more than your tolerance — if they do not, this input cannot "
                "discriminate and the INPUT has to change instead of the oracle. "
                "A third guessed expected value will fail the same way the first "
                "two did.")
    if verdict.reproduction == V.P2P:
        return ("Both revisions PASSED, so nothing here distinguishes them: your "
                "input does not reach the defect. Nothing in this output can be "
                "used to fix the oracle — change the input.")
    if verdict.reproduction == V.P2F:
        return ("The FIXED revision's failure below names the assertion that only "
                "holds on the buggy revision. That is the one to delete.")
    if verdict.reproduction == V.F2P and buggy:
        return ("The transition is right. The buggy side's values below say "
                "whether it failed for the reported reason or for another one.")
    return ""


def measurements(verdict: V.Verdict, *, task: str = "",
                 buggy_output: str = "", fixed_output: str = "",
                 tolerance_window: dict | None = None) -> dict:
    sides = {}
    for name, side, text in (("buggy_revision", verdict.buggy, buggy_output),
                             ("fixed_revision", verdict.fixed, fixed_output)):
        entry = {"test_outcome": side.test_outcome}
        if side.failure_type:
            entry["failure_type"] = side.failure_type
        if side.values:
            entry["values"] = side.values
        excerpt = V.failure_excerpt(text)
        if excerpt:
            entry["printed_comparison"] = excerpt
        sides[name] = entry
    if not any(e.get("values") or e.get("printed_comparison")
               for e in sides.values()):
        return {}
    window = tolerance_window or _gap_from_sides(verdict) \
        or V.tolerance_window(buggy_output, fixed_output)
    if window:
        sides["tolerance_window"] = window
    note = _reading(verdict, task, window)
    if note:
        sides["how_to_read_this"] = note
    return sides

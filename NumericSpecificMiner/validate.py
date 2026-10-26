"""Curator-side checks on a mined context, against the patch.

The patch is WITHHELD from the agent, not from the benchmark builder. That
asymmetry is what makes these checks possible and also what bounds them: a
problem may be *reported*, and the miner may be asked to look again in the
allowed sources, but nothing derived from the diff may be written into the
context. `affected_api` recovered from the developer's test would hand the agent
the one thing BRTG asks it to predict.

So every function here returns findings. None of them edits the context.

What they are for, concretely:

* scipy#19933's `affected_api` is `scipy.special.nrdtrimn`; the patch touches
  `_cdflib_wrappers.pxd` and the developer's test calls `stdtr`/`stdtrit`. The
  agent spent twelve probes on a function the fix never touches.
* cvxpy#3513's is `SuppFuncAtom._value_impl`; the test asserts on
  `SCS.parse_solver_options`.
* cvxpy#3390's names the right helper but the example frames the bug at the
  user level, so the generated test solved a problem instead of calling it.
* scipy#19933's example states `buggy_output: NaN` where NaN is the CORRECT
  result — an inverted oracle, which no tolerance can rescue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: `def foo(`, `foo = `, `class Foo`, and dotted call sites `mod.foo(`
_DEF = re.compile(r"^\+\s*(?:def|class)\s+([A-Za-z_]\w*)", re.M)
_CALL = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\(")
#: a diff's own file headers, which name the module that changed
_FILE = re.compile(r"^\+\+\+ b/(\S+)", re.M)

#: words that are never the API under test
_NOISE = frozenset("""
assert assert_allclose assert_array_equal assert_array_almost_equal
assert_almost_equal approx raises warns pytest np numpy print len range int
float str list dict set tuple bool abs max min sum sorted enumerate zip map
parametrize fixture mark array zeros ones eye empty arange linspace isnan
isfinite isinf allclose
""".split())


@dataclass
class Findings:
    instance: str = ""
    problems: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"instance": self.instance, "ok": self.ok,
                "problems": self.problems, "notes": self.notes}


def _symbols(patch_text: str, added_only: bool = True) -> set:
    """Plausible API names a patch defines or calls."""
    text = patch_text or ""
    out = set(_DEF.findall(text))
    lines = [ln[1:] for ln in text.splitlines() if ln.startswith("+")] \
        if added_only else text.splitlines()
    for line in lines:
        for hit in _CALL.findall(line):
            leaf = hit.split(".")[-1]
            if leaf and leaf not in _NOISE and not leaf.startswith("test_"):
                out.add(hit)
                out.add(leaf)
    return {s for s in out if s not in _NOISE}


def _leaves(name: str) -> set:
    """`scipy.special.nrdtrimn` -> {scipy, special, nrdtrimn} minus noise."""
    parts = re.split(r"[^A-Za-z_0-9]+", name or "")
    return {p for p in parts if p and p not in _NOISE and len(p) > 2}


def check_affected_api(affected_api, source_patch: str, test_patch: str) -> Findings:
    """Is the API the context names actually the one the fix and test touch?"""
    f = Findings()
    named = _leaves(str(affected_api or ""))
    if not named:
        f.problems.append({
            "field": "numerical_failure.affected_api",
            "problem": "no affected API is named, so a generated test has "
                       "nothing to call and must guess the entry point",
            "action": "re-ask the failure question for the API, from the PR "
                      "body and the linked issue only"})
        return f
    patch_syms = _symbols(source_patch) | _symbols(test_patch)
    patch_files = {m.rsplit("/", 1)[-1].split(".")[0]
                   for m in _FILE.findall(source_patch or "")}
    haystack = {s.split(".")[-1] for s in patch_syms} | patch_files
    if named & haystack:
        f.notes.append(f"affected_api grounded by {sorted(named & haystack)[:3]}")
        return f
    # Not grounded. Say so, and say what the patch DOES touch — for the
    # curator's eyes, as a label on the instance, never written into the context.
    public = sorted({s for s in patch_syms if "." not in s}, key=len)[:6]
    f.problems.append({
        "field": "numerical_failure.affected_api",
        "problem": f"the context names {str(affected_api)[:70]!r}, which appears "
                   f"nowhere in the fix or its test — a generated test aimed at "
                   f"it exercises code the patch never changed",
        "patch_touches": sorted(patch_files)[:4],
        "patch_symbols_for_the_curator_only": public,
        "action": "re-ask the failure question for the API; if the allowed "
                  "sources do not name it, label the instance BRTG-ineligible "
                  "rather than supplying the symbol from the diff"})
    return f


def check_examples(examples, test_patch: str) -> Findings:
    """Do the examples carry a usable, correctly-directed oracle?"""
    f = Findings()
    rows = [e for e in (examples or []) if isinstance(e, dict)]
    if not rows:
        f.problems.append({
            "field": "numerical_failure.input_output_examples",
            "problem": "no concrete (input, buggy output, expected output) "
                       "triple, so the only available oracle is a property "
                       "general enough to hold on both revisions",
            "action": "re-ask for one concrete triple"})
        return f
    for i, row in enumerate(rows):
        where = f"input_output_examples[{i}]"
        text = " ".join(str(row.get(k) or "") for k in
                        ("input", "buggy_output", "expected_output"))
        if re.search(r"not (?:fully )?shown|not available|exact \w+ not|"
                     r"not given|unclear|unknown", text, re.I):
            f.problems.append({
                "field": where,
                "problem": "the example SAYS its own values are missing, so it "
                           "cannot be used as an oracle",
                "action": "search the linked issue for the values; if they are "
                          "only in the test, label the instance BRTG-ineligible"})
        if not str(row.get("expected_output") or "").strip():
            f.problems.append({
                "field": where,
                "problem": "no expected output, so the test can state what is "
                           "wrong but not what is right",
                "action": "re-ask for the expected value or the property"})
        # inverted direction: the example calls the CORRECT result the buggy one
        buggy = str(row.get("buggy_output") or "").lower()
        if re.search(r"\b(?:nan|inf|-inf)\b", buggy) and test_patch:
            asserted_special = re.search(
                r"^\+.*\bassert[^\n]*\bis(?:nan|inf|finite)\b", test_patch,
                re.M | re.I)
            if asserted_special:
                f.problems.append({
                    "field": where,
                    "problem": f"the example reports {buggy[:24]!r} as the BUGGY "
                               f"output, but the developer's test asserts that "
                               f"special value is CORRECT — the oracle is "
                               f"inverted, and a test built on it passes on "
                               f"buggy and fails on fixed",
                    "action": "re-ask for the direction: which revision produces "
                              "the special value"})
    if not f.problems:
        f.notes.append(f"{len(rows)} example(s), each with an expected output")
    return f


def validate(context, source_patch: str = "", test_patch: str = "") -> Findings:
    """Every curator-side check on one mined context."""
    nf = getattr(context, "numerical_failure", {}) or {}
    nf = nf if isinstance(nf, dict) else {}
    out = Findings(instance=f"{getattr(context, 'repo', '')}#"
                            f"{getattr(context, 'pr_number', '')}")
    for part in (check_affected_api(nf.get("affected_api"), source_patch, test_patch),
                 check_examples(nf.get("input_output_examples"), test_patch)):
        out.problems.extend(part.problems)
        out.notes.extend(part.notes)
    return out

from __future__ import annotations

import re

from .agent.llm.base import LLMClient, complete_json
from .probe_contracts import validate_probe
from .repro_probe_langs import CODE_CONTRACT, EXAMPLE_ERROR, EXPECTED_PREFIX, canonical

#: PYTHON ONLY, deliberately. The benchmark targets Python instances, and a
#: prompt that has to hold for Python, Go, Rust, Java and C at once states each
#: contract loosely — "record with the injected probe API" instead of
#: `probe(name, value)`, "isolate each example" instead of try/except — and a
#: model writing Python gets a worse brief for the sake of languages this corpus
#: does not contain. A caller handed another language keeps the template path
#: (javascript/typescript) or the ordinary `run_numerical_probe`, exactly as
#: before this module existed.
LANGUAGE = "python"

#: How many times a rejected probe is handed back with the reason. Three is what
#: the preamble loop used: enough for an import fix or a missed example, not so
#: many that a model which cannot write this probe burns the run's budget.
MAX_ATTEMPTS = 3

#: A record name and an example input pair on alphanumerics alone — the same
#: normalization `repro_compare._lookup_record` uses to match them. Coverage is
#: checked on that basis so a model that writes the name with different spacing
#: or quoting is not rejected for something the comparator tolerates.
def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(text).lower())


SYSTEM = (
    "You write NUMERICAL PROBES in PYTHON: one script that runs inside a "
    "checked-out revision of a repository and records what its code computes. "
    "The revision's directory is the working directory and is first on "
    "sys.path, so a plain `import <package>` resolves to THIS revision. "
    "`probe(name, value)` is injected as a builtin — do not define or import "
    "it.\n\n"
    "You are given the input/output examples a bug report stated. Write ONE "
    "script that evaluates every example against this revision and records what "
    "it produced. The same source is run on the buggy revision and on the fixed "
    "one and the two runs are compared value by value, so the script must not "
    "branch on which revision it is in or assume either outcome.\n\n"
    "RULES, in the order they matter:\n"
    "1. RECORD NAME. Record each example under the example's own input text, "
    "exactly as given to you: `probe(\"<the input text>\", value)`. The "
    "comparator pairs the two revisions' observations by that name; a renamed "
    "record is an unobserved example.\n"
    "2. ISOLATE. Put each example in its own try/except so one failure cannot "
    "stop the rest. In the except branch record "
    f"`\"{EXAMPLE_ERROR}\" + type(e).__name__ + \": \" + str(e)` under the "
    "same name — an exception IS the observation for an example whose reported "
    "difference is a raised error.\n"
    "3. EXPECTED VALUES. When the report's expected output is itself a Python "
    "expression (not prose), evaluate it too and record it under "
    f"`\"{EXPECTED_PREFIX}\" + the same name`, in its own try/except. Skip it "
    "when the expectation is prose.\n"
    "4. NO ASSERTIONS. Record values; do not assert, and do not decide whether "
    "the revision is correct. An exception at top level ends the script and "
    "records nothing, so nothing outside a try/except may raise.\n"
    "5. SETUP IS CODE. Imports, fixtures, tmp files, several statements for one "
    "example — write them. You are not limited to one expression per example, "
    "and you may record several values for one example (the value, its dtype, "
    "its repr) under names that begin with the example's own text.\n"
    "6. An example you genuinely cannot express as code (it describes a manual "
    "procedure, or names no concrete input) goes in `unusable`, not into the "
    "probe as a guess.\n\n"
    "WHAT THE REPORT SAYS THE TWO REVISIONS DO is given to you as well: the "
    "behaviour expected on the buggy revision, the behaviour expected after the "
    "fix, and the run's success criterion. Use them to decide WHAT TO RECORD, "
    "never what to conclude. They tell you which quantity separates the two "
    "revisions — the formatted string rather than the object, the dtype as well "
    "as the value, the element that loses precision, the exception type — so "
    "record that quantity, at the precision that makes the difference visible. "
    "An example whose stated difference is a raised exception is recorded by "
    "recording the exception's type and message, which rule 2 already does. "
    "Never test for the expected outcome, never branch on which revision this "
    "is, and never record a pass/fail verdict: the comparator decides that by "
    "putting the two revisions' records side by side."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string",
                 "description": "the complete probe source, in the revision's language"},
        "unusable": {"type": "array", "items": {"type": "string"},
                     "description": "example inputs that cannot be expressed as code, "
                                    "verbatim as given"},
        "notes": {"type": "string",
                  "description": "one line: what the probe imports and how it reaches "
                                 "the code under test"},
    },
    "required": ["code"],
}


def _request(examples: list, hints: str, retry: str = "",
             behavior: dict | None = None) -> str:
    """The user half of the codegen call."""
    lines = [f"PROBE CONTRACT: {CODE_CONTRACT[LANGUAGE]}", ""]
    behavior = behavior or {}
    for key, label in (("summary", "WHAT THE BUG IS"),
                       ("expected_buggy_behavior", "EXPECTED ON THE BUGGY REVISION"),
                       ("expected_fixed_behavior", "EXPECTED AFTER THE FIX"),
                       ("success_criterion", "SUCCESS CRITERION FOR THIS RUN")):
        text = str(behavior.get(key) or "").strip()
        if text:
            lines += [f"{label}:", f"  {text}", ""]
    if hints:
        lines += [hints.strip(), ""]
    lines.append("EXAMPLES FROM THE REPORT — record each one under the name shown:")
    for raw, buggy, want in examples:
        lines.append(f"  name: {raw}")
        if buggy:
            lines.append(f"    report says the buggy revision gives: {buggy}")
        if want:
            lines.append(f"    report says it should give: {want}")
    if retry:
        lines += ["", "YOUR PREVIOUS PROBE WAS REJECTED WITHOUT RUNNING:", retry,
                  "", "Return the corrected probe."]
    return "\n".join(lines)


#: The error marker as it must appear in generated source. A probe writes this
#: itself, and everything downstream keys on it: the comparator reads a record
#: carrying it as "this example was not observed", and the repair loop rewrites
#: a probe whose examples carry it on BOTH revisions. `f"__error__:{type(e)}"`
#: — the marker without its space — is a near-miss a model makes readily, and on
#: statsmodels#10214 it turned three crashed examples into three "observed"
#: values that agreed on both revisions, while hiding them from the repair loop.
#: Matching is tolerant at READ time (repro_probe_langs.is_example_error); here,
#: at WRITE time, the exact spelling is required, because the generator is the
#: one place that can still ask for it to be corrected.
_ERROR_MARKER = re.compile(r"__error__(.{0,2})")


def marker_misspelt(code: str) -> str:
    """The wrong spelling of the error marker in `code` ("" when it is right)."""
    for tail in _ERROR_MARKER.findall(code or ""):
        if not tail.startswith(": "):
            return "__error__" + tail
    return ""


def missing_records(code: str, names) -> list:
    """Example names the source never records, by the comparator's own matching.

    Checked on the normalized name so quoting and whitespace do not matter, and
    substring-wise because a probe legitimately records
    ``pd.Series([2, 3]).astype("m8[s]")[0]`` for the example
    ``pd.Series([2, 3]).astype("m8[s]")``.
    """
    body = _norm(code)
    return [n for n in names if _norm(n) and _norm(n) not in body]


def generate_example_probe(client: LLMClient, *, examples: list,
                           language: str = LANGUAGE, behavior: dict | None = None,
                           hints: str = "", feedback: str = "",
                           attempts: int = MAX_ATTEMPTS, on_attempt=None) -> dict:
    """Ask the model to write the examples probe; return it only if it validates.

    `examples` is a list of ``(input, buggy_output, expected_output)`` as the
    report stated them. `behavior` carries the rest of the mined strategy —
    ``summary``, ``expected_buggy_behavior``, ``expected_fixed_behavior``,
    ``success_criterion`` — which says which quantity actually separates the two
    revisions. The examples alone often do not: an example may read
    ``Angle('1d2m59.49999s').to_string(precision=0)`` while the difference lives
    in the formatted string's carry ('1d03m00s' vs '1d02m59s'), and a probe that
    records the Angle object rather than its formatting observes nothing.

    Returns ``{"code", "unusable", "notes", "attempts"}``, or ``{"error",
    "attempts", "rejections"}`` when nothing usable came back — in which case the
    caller still has the template path for python/javascript.

    `feedback` reopens a probe that already RAN and failed — the errors its
    examples raised, handed back the way a rejected contract is. Validation can
    only see that a probe is shaped like a probe; that `from jax._src import xla`
    does not exist in this revision is something only the run finds out.

    `language` exists to be checked, not to be varied: see :data:`LANGUAGE`.
    """
    lang = canonical(language) or language
    if lang != LANGUAGE:
        return {"error": f"probe generation is {LANGUAGE}-only; {lang!r} keeps the "
                         f"template probe / run_numerical_probe path",
                "attempts": 0, "rejections": []}
    names = [str(raw) for raw, _, _ in examples if str(raw).strip()]
    retry, rejections = feedback, []
    for attempt in range(1, attempts + 1):
        raw = complete_json(client, SYSTEM,
                            _request(examples, hints, retry, behavior),
                            SCHEMA, "submit_probe",
                            "Submit the probe source for the reported examples.")
        code = str(raw.get("code") or "").strip()
        unusable = [str(u) for u in (raw.get("unusable") or [])]
        if on_attempt:
            on_attempt(attempt, code)
        if not code:
            retry = "You returned no code."
            rejections.append(retry)
            continue
        # LAYER 1 — the language's probe contract: is this structurally a probe
        # of this language at all? Same check the probe tool applies, run here so
        # the rejection costs a model call instead of a container run.
        contract = validate_probe(lang, code)
        if not contract.valid:
            retry = contract.feedback(lang)
            rejections.append("; ".join(contract.errors))
            continue
        # LAYER 2 — the pairing contract. A probe whose records are named
        # anything else runs fine and observes nothing, which is the failure this
        # whole module exists to prevent; the model that wrote it can fix it.
        wrong = marker_misspelt(code)
        if wrong:
            retry = (f"The error marker is written {wrong!r}. It must be exactly "
                     f"\"{EXAMPLE_ERROR}\" — marker, colon, ONE space — because "
                     f"the comparator keys on it to tell an example that could "
                     f"not be evaluated from one that produced a value. Write "
                     f"`\"{EXAMPLE_ERROR}\" + type(e).__name__ + \": \" + str(e)`.")
            rejections.append(f"error marker written as {wrong!r}")
            continue
        wanted = [n for n in names if n not in unusable]
        gaps = missing_records(code, wanted)
        if gaps:
            retry = ("These examples are not recorded under their own names — the "
                     "comparator pairs the two revisions by record name, so each "
                     "one must appear verbatim as the name argument of a record "
                     "call (or be listed in `unusable`):\n"
                     + "\n".join(f"  {g}" for g in gaps[:8]))
            rejections.append(f"{len(gaps)} example(s) unrecorded")
            continue
        return {"code": code, "unusable": unusable, "attempts": attempt,
                "notes": str(raw.get("notes") or ""), "rejections": rejections}
    return {"error": f"no probe passed validation in {attempts} attempts",
            "attempts": attempts, "rejections": rejections}

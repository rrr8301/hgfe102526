"""Structured evidence completeness: what a reproduction needs, and whether we have it.

The ablation that motivates this package found full-context prompting beating
compact RAG on F2P while costing far more tokens. The gap is not that the
compact context is short — it is that it is sometimes short of the *particular*
facts a reproduction turns on. So completeness is measured per task and per
category, against a checklist, before a single token is spent on a strategy:

* a **required** field missing is a known hole; the miner re-asks for it;
* an **expected** field missing is a risk; it is reported and carried;
* a field that is null **because the evidence is silent** is distinguished from
  one that is null because nobody asked — `asked` is recorded per question.

The checklist is deliberately small and reproduction-shaped. Each entry names a
field path in the context and says which task needs it, so HBEC is not held to
BRTG's oracle requirements or the reverse.
"""
from __future__ import annotations

from dataclasses import dataclass

from .context import NumericalContext

HBEC, BRTG = "hbec", "brtg"


@dataclass(frozen=True)
class Requirement:
    path: str                 # dotted path into the context
    tasks: tuple              # which task(s) need it
    level: str                # "required" | "expected"
    why: str                  # what breaks without it
    categories: tuple = ()    # empty = all categories


#: What a reproduction of a numerical bug actually needs. Short on purpose: a
#: checklist that lists everything measures nothing.
CHECKLIST: tuple[Requirement, ...] = (
    # --- environment: can the revision be built and its tests run? ----------
    Requirement("environment.test_commands", (HBEC,), "required",
                "without the test command the harness cannot run the oracle at all"),
    Requirement("environment.dependencies", (HBEC,), "required",
                "an unpinned numerical dependency silently changes the result"),
    Requirement("environment.language_version", (HBEC,), "expected",
                "the interpreter version decides whether the revision builds"),
    Requirement("environment.build_commands", (HBEC,), "expected",
                "a compiled project that is not built is not the revision under test"),
    Requirement("environment.numerical_backend", (HBEC,), "expected",
                "BLAS/LAPACK choice changes results for linear algebra instances",
                ("accuracy", "convergence")),

    # --- failure: is there anything to observe, and a way to judge it? ------
    Requirement("numerical_failure.affected_api", (BRTG,), "required",
                "the probe has nothing to call without the API under test"),
    Requirement("numerical_failure.observed_behavior", (BRTG,), "required",
                "no statement of the wrong behaviour means no defect to look for"),
    Requirement("numerical_failure.expected_behavior", (BRTG,), "required",
                "without the expected behaviour the probe cannot judge what it sees"),
    Requirement("numerical_failure.trigger_conditions", (BRTG,), "required",
                "an input that misses the defective path reproduces nothing"),
    # REQUIRED, not expected. Of the BRTG runs that failed on oracle quality,
    # every one whose test passed on both revisions (P2P) had an empty
    # input_output_examples: with no concrete triple the agent can only assert a
    # property it inferred from prose, and a property vague enough to infer is
    # usually true on both revisions. An example is not a nicety here, it is the
    # oracle — so a missing one is a hole the ladder must climb for, not a risk
    # to carry. (It is not sufficient on its own: scipy#20073 failed holding
    # five. But its absence is the one thing the P2P failures share.)
    Requirement("numerical_failure.input_output_examples", (BRTG,), "required",
                "a concrete (input, buggy output, expected output) triple IS the "
                "oracle; without one the generated test can only assert a "
                "property vague enough to hold on both revisions"),
    Requirement("numerical_failure.manifestation", (BRTG,), "expected",
                "a probe looking for a wrong value cannot see a raised exception"),

    # --- characteristics: category-specific, and this is where RAG usually
    #     falls short of full context ----------------------------------------
    Requirement("numerical_characteristics.dtype", (BRTG,), "required",
                "the defect appears for one dtype and not another",
                ("accuracy", "special_value")),
    Requirement("numerical_characteristics.tolerance", (BRTG,), "required",
                "a 1-ULP defect is invisible at a default tolerance",
                ("accuracy",)),
    Requirement("numerical_characteristics.magnitude", (BRTG,), "expected",
                "the regime where the discrepancy appears", ("accuracy",)),
    Requirement("numerical_characteristics.category_specific_evidence", (BRTG,),
                "expected", "the category's own properties the probe must respect"),
)


def _get(ctx: NumericalContext, path: str):
    node: object = ctx.to_dict()
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _filled(value) -> bool:
    return value not in (None, "", [], {}, "null", "unknown", "N/A")


def applicable(req: Requirement, task: str, category: str) -> bool:
    return task in req.tasks and (not req.categories or category in req.categories)


def assess(ctx: NumericalContext, task: str = BRTG) -> dict:
    """What this context has and lacks for `task`.

    Returns `{score, required_missing, expected_missing, present, checked}` —
    `score` counts required fields only, because an expected field is a risk and
    a required one is a hole.
    """
    required_missing, expected_missing, present = [], [], []
    checked = 0
    for req in CHECKLIST:
        if not applicable(req, task, ctx.category):
            continue
        checked += 1
        if _filled(_get(ctx, req.path)):
            present.append(req.path)
        elif req.level == "required":
            required_missing.append({"path": req.path, "why": req.why})
        else:
            expected_missing.append({"path": req.path, "why": req.why})
    n_required = sum(1 for r in CHECKLIST
                     if applicable(r, task, ctx.category) and r.level == "required")
    got = n_required - len(required_missing)
    return {
        "task": task,
        "category": ctx.category,
        "checked": checked,
        "score": round(got / n_required, 3) if n_required else 1.0,
        "required_total": n_required,
        "required_present": got,
        "required_missing": required_missing,
        "expected_missing": expected_missing,
        "present": present,
    }


def both_tasks(ctx: NumericalContext) -> dict:
    """Completeness for HBEC and BRTG side by side — DP2's two consumers."""
    return {HBEC: assess(ctx, HBEC), BRTG: assess(ctx, BRTG)}


#: which question can still supply a missing field, so a gap becomes one
#: targeted re-ask rather than a second full pass
QUESTION_FOR = {
    "environment": "environment",
    "numerical_failure": "failure",
    "numerical_characteristics": "characteristics",
}


def gaps_by_question(report: dict) -> dict:
    """`{question_id: [paths]}` for the required gaps in an assessment."""
    out: dict = {}
    for gap in report.get("required_missing", []):
        qid = QUESTION_FOR.get(gap["path"].split(".", 1)[0])
        if qid:
            out.setdefault(qid, []).append(gap["path"])
    return out


def gap_instruction(paths: list, report: dict) -> str:
    """The sentence a repair pass is given: what is missing and why it matters."""
    why = {g["path"]: g["why"] for g in report.get("required_missing", [])}
    lines = [f"- {p}: {why.get(p, 'required for reproduction')}" for p in paths]
    return ("The first pass left these REQUIRED fields empty. Search the passages "
            "again specifically for them, and fill only those you can ground:\n"
            + "\n".join(lines)
            + "\nIf the evidence genuinely does not state a field, leave it null and "
              "say so in the relevant list field — a guess is worse than a null.")

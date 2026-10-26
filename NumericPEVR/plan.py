from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from numagent.repro_workspace import BUGGY_DIR, FIXED_DIR

from . import boundaries as B

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "enum": [B.HBEC, B.BRTG]},
        "objective": {"type": "string"},
        "evidence_used": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "step": {"type": "string"},
                "revision": {"type": "string", "enum": ["buggy", "fixed", "both"]},
                "command": {"type": "string"},
                "file": {"type": "string",
                         "description": "path, relative to the revision root, "
                                        "to write before the commands run"},
                "contents": {"type": "string",
                             "description": "the file's exact contents"},
                "purpose": {"type": "string",
                            "enum": ["setup", "build", "test", "probe", "inspect",
                                     "rebuild"]},
                "python_version": {"type": "string",
                                   "description": "purpose 'rebuild' only: the CPython "
                                                  "to rebuild the revision on, e.g. '3.11'"},
                "pins": {"type": "array", "items": {"type": "string"},
                         "description": "purpose 'rebuild' only: dependency pins to "
                                        "rebuild with, e.g. [\"numpy<2.4\"]"},
            },
            "required": ["step", "revision", "purpose"]}},
        "success_criterion": {"type": "string"},
    },
    "required": ["task", "objective", "evidence_used", "actions", "success_criterion"],
}

SYSTEM = (
    "You plan the reproduction of a CONFIRMED numerical bug from a compact "
    "evidence context. Produce a SHORT structured plan — no prose explanation. "
    "Every field you rely on must be named in evidence_used, using its dotted "
    "path in the context; anything you supply that the context does not state "
    "goes in assumptions, phrased so a reader can see it was inferred. Respect "
    "the task boundary exactly: a reproduction obtained by changing what the "
    "task holds fixed is not a reproduction."
)

OBJECTIVES = {
    B.HBEC: "Reconstruct E_B and E_F so the developer test yields a valid F2P",
    B.BRTG: "Generate T_gen that yields a valid F2P in the frozen environments",
}

SHELL_RULE = (
    f"HOW COMMANDS RUN: each command runs in a FRESH shell, already inside the "
    f"working directory of the revision you addressed it to — {BUGGY_DIR} for "
    f"buggy, {FIXED_DIR} for fixed, each a complete checkout with the "
    f"reproducing-test patch applied. Do NOT `cd` to a path of your own and do "
    f"not guess a directory name: address the revision with the action's "
    f"`revision` field and give the command a path relative to the checkout "
    f"root (`tests/foo_test.py::case`). Because each shell is new, an `export` on "
    f"its own line is discarded before the next command starts — put the variable "
    f"on the same line as the command that needs it (`FOO=1 pytest …`), or inside "
    f"a single `sh -c '…'`.\n"
    f"ONE EXCEPTION, and it matters: a command that IMPORTS the package under "
    f"test must first leave the checkout, because the checkout root shadows the "
    f"built package: from {BUGGY_DIR}, `import numpy` resolves to the unbuilt "
    f"source directory sitting there rather than to the built package in the "
    f"venv, and fails with `No module named` or a half-initialised module. So "
    f"write a measurement or inspection one-liner as "
    f"`cd /tmp && python -c \"import …; print(repr(…))\"`. The revision's "
    f"interpreter comes from its venv on PATH, not from the directory, so "
    f"leaving the tree keeps you on the right revision. Test files are NOT "
    f"affected: address those by their path relative to the checkout root as "
    f"above, and the harness runs them from a neutral directory for you.\n"
    f"WRITING A PROBE SO IT SURVIVES ITS OWN SURPRISES: a probe is ONE "
    f"`python -c`, so the FIRST unhandled exception discards every measurement "
    f"after it — including the ones that would have answered your question. "
    f"Wrap each name and each call in its own `try/except Exception as e: "
    f"print(name, 'ERR', type(e).__name__, e)` so one bad guess costs you that "
    f"line and not the iteration, and never put several lookups inside a single "
    f"list comprehension, which cannot catch anything. In particular, do not "
    f"call `inspect.signature` on a name you have not confirmed is callable: a "
    f"module-level CONSTANT is a common and legitimate answer to `hasattr`, and "
    f"`inspect.signature(3.14)` raises TypeError. mpmath#1164 lost all four of "
    f"its iterations to exactly this — `blog2_10` is a float, the probe asked "
    f"for its signature inside a comprehension, and the TypeError threw away the "
    f"base-by-base comparison that was the entire point of the probe.")

VERDICT_RULE = {
    "f2p_only": (
        "HOW THIS RUN IS GRADED: by the test transition ALONE. The selected test "
        "must FAIL on the buggy revision and PASS on the fixed one, and the "
        "buggy-side failure must be a real test failure rather than a setup or "
        "collection error. Nothing is probed afterwards and no reported value is "
        "re-checked, so printing or comparing numbers earns nothing — spend every "
        "action on making that transition happen for the right reason."),
    "full": (
        "HOW THIS RUN IS GRADED: the transition is necessary but NOT sufficient. "
        "After it, numerical probes must show the behaviour the report describes, "
        "so a transition earned by an unrelated failure does not count."),
    "brtg": (
        "HOW THIS RUN IS GRADED: by YOUR test's transition — it must FAIL on the "
        "buggy revision and PASS on the fixed one, and the buggy-side failure "
        "must be a numerical assertion of the kind the report describes, not an "
        "incidental error. Nothing else is probed; the test is the deliverable."),
}


@dataclass
class Plan:
    task: str
    objective: str = ""
    evidence_used: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    success_criterion: str = "VALID_F2P"
    error: str = ""

    def as_dict(self) -> dict:
        return {"task": self.task, "objective": self.objective,
                "evidence_used": self.evidence_used, "assumptions": self.assumptions,
                "actions": self.actions, "success_criterion": self.success_criterion,
                **({"error": self.error} if self.error else {})}

    def writes(self) -> list:
        out = []
        for a in self.actions:
            path, body = a.get("file"), a.get("contents")
            if path and body is not None:
                out.append((str(path), str(body), a.get("revision", "both")))
        return out

    def commands(self) -> list:
        out = []
        for a in self.actions:
            if a.get("command") and a.get("purpose") != "rebuild":
                out.append((a["command"], a.get("revision", "buggy"),
                            a.get("purpose", "inspect")))
        return out

    def agent_builds(self) -> list:
        out = []
        for a in self.actions:
            version = str(a.get("python_version") or "").strip()
            pins = [str(p).strip() for p in (a.get("pins") or []) if str(p).strip()]
            recipe = str(a.get("recipe") or "").strip().lower()
            if a.get("purpose") == "rebuild" and (version or pins or recipe):
                out.append((a.get("revision", "both"), version, pins, recipe))
        return out

    def rebuilds(self) -> list:
        out = []
        for a in self.actions:
            version = str(a.get("python_version") or "").strip()
            pins = [str(p).strip() for p in (a.get("pins") or []) if str(p).strip()]
            if a.get("purpose") == "rebuild" and (version or pins):
                out.append((a.get("revision", "both"), version, pins))
        return out


def _value_kinds(context) -> set:
    nf = getattr(context, "numerical_failure", {}) or {}
    if not isinstance(nf, dict):
        return set()
    out = set()
    for row in (nf.get("input_output_examples") or []):
        if isinstance(row, dict):
            kind = (row.get("value_kind") or "").strip().lower()
            if kind and kind != "unknown":
                out.add(kind)
    return out


def _has_concrete_input(context) -> bool:
    nf = getattr(context, "numerical_failure", {}) or {}
    if not isinstance(nf, dict):
        return False
    for key in ("input_output_examples", "trigger_conditions"):
        value = nf.get(key)
        if isinstance(value, list) and any(
                v for v in value if v not in (None, "", {}, [])):
            return key == "input_output_examples" or _looks_concrete(value)
    return False


def _looks_concrete(entries) -> bool:
    text = " ".join(str(e) for e in entries)
    return bool(re.search(r"[-+]?\d*\.?\d+[eE][-+]?\d+|=\s*[-+]?\d", text))


REBUILD_RULE = (
    "CHANGING A REVISION'S PYTHON: each revision runs in the venv the harness "
    "built for it, and every command and test resolves that venv first, so a venv "
    "you create yourself is never the one tested, and reinstalling the package "
    "over the prepared build is refused. When the evidence says the bug needs a "
    "different interpreter (e.g. it triggers only on Python 3.11) or the build is "
    "on the wrong one, plan an action with purpose \"rebuild\", the revision "
    "(usually \"both\", so the comparison stays like for like) and "
    "python_version (e.g. \"3.11\"), and no command: the harness then rebuilds that "
    "revision's venv on that CPython, installing it if absent. Rebuild actions run "
    "before the plan's commands. First check the current interpreter "
    "(`cd /tmp && python -V`); rebuild only when it is the wrong one.\n"
    "CHANGING A PINNED DEPENDENCY: numpy, pandas and the other era-pinned packages "
    "are fixed by a constraints file that every pip in the revision obeys, so "
    "`pip install numpy==…` is rejected, and a numpy swapped under a COMPILED "
    "package breaks its extensions anyway. To change one, rebuild with \"pins\" "
    "(e.g. {\"purpose\": \"rebuild\", \"revision\": \"both\", \"pins\": [\"numpy<2.4\"]}; "
    "python_version may be left out): the harness re-resolves that dependency to "
    "the newest release matching the pin as of the commit's date and rebuilds "
    "the package against it. When a release REMOVED an API the revision uses "
    "(`module 'numpy' has no attribute 'trapz'`), step down ONE minor version "
    "below the current pin, never straight to an older major.")


AGENT_PLAN_SCHEMA = json.loads(json.dumps(PLAN_SCHEMA))
AGENT_PLAN_SCHEMA["properties"]["actions"]["items"]["properties"]["recipe"] = {
    "type": "string", "enum": ["compiled", "pure"],
    "description": "purpose 'rebuild' only: how to build the revision. 'compiled' "
                   "when the package has extension modules that must be compiled "
                   "before it can be imported (C/C++/Cython/Fortran sources, "
                   "meson.build, setup.py ext_modules); 'pure' when it imports "
                   "straight from its source tree"}

AGENT_BUILD_RULE = (
    "BUILDING IS YOURS: neither revision has been built or installed. You decide "
    "how. Each revision is built through a \"rebuild\" action with a \"recipe\": "
    "\"compiled\" (the package has extension modules that must be compiled — C, "
    "C++, Cython or Fortran sources, a meson.build, setup.py ext_modules — and the "
    "harness compiles it into that revision's own venv with the era's build "
    "tools) or \"pure\" (it imports from its source tree, and the harness makes "
    "an isolated venv and installs it editable with its test dependencies). Add "
    "python_version and/or pins as needed. Decide from the evidence: the "
    "context's environment, and the checkout itself (`cat pyproject.toml`, "
    "`ls`, `find . -name '*.pyx' | head`). Rebuild actions run BEFORE a plan's "
    "commands, so if you need to look first, make that plan inspection only. "
    "Build BOTH revisions with the same recipe. After every build the harness "
    "checks that the revision's interpreter imports that revision's own code and "
    "that buggy and fixed do not share one; a build failing that check is "
    "reported as failed. The developer test is refused on a revision you have "
    "not built.")


def build_prompt(context, task: str, *, state=None, diagnosis=None,
                 extra_evidence: str = "", grading: str = "",
                 test_identity: str = "", measurements: dict | None = None,
                 build_mode: str = "") -> str:
    parts = [f"OBJECTIVE: {OBJECTIVES[task]}",
             B.describe(task)]
    if grading in VERDICT_RULE:
        parts.append(VERDICT_RULE[grading])
    if test_identity:
        parts += ["", test_identity]
    parts += ["", SHELL_RULE]
    if task == B.HBEC:
        parts += ["", REBUILD_RULE]
        if build_mode == "agent":
            parts += ["", AGENT_BUILD_RULE]
    parts += ["",
             "REPRODUCTION CONTEXT (all that is known; a null field was not "
             "stated by the evidence — plan around it, do not invent it):",
             context.to_prompt() if hasattr(context, "to_prompt") else str(context)]
    if state is not None and state.iteration:
        parts += ["", "STATE (this is a REPLAN; the full logs are deliberately not "
                      "included):", json.dumps(state.feedback(), indent=1)]
    if diagnosis is not None:
        moves = {"diagnosis": diagnosis.diagnosis,
                 "do": diagnosis.instruction,
                 "forbidden": list(diagnosis.forbids)}
        parts += ["", "DIAGNOSIS AND THE ONE CHANGE TO MAKE:",
                  json.dumps(moves, indent=1)]
    if measurements:
        parts += ["", "MEASURED OUTPUT OF YOUR PREVIOUS RUN (facts from your own "
                      "two executions, not retrieved evidence — where these "
                      "contradict a value you inferred, THESE are right):",
                  json.dumps(measurements, indent=1)]
    if extra_evidence:
        parts += ["", "EVIDENCE RETRIEVED FOR THIS ITERATION:", extra_evidence]
    parts += ["", "Return the plan as JSON per the schema. Keep it to the few "
                  "actions this iteration needs — one change, then verify."]
    if task == B.BRTG:
        parts += [
            "ORACLE DIRECTION: every assertion in your test states the CORRECT "
            "behaviour — what the FIXED revision produces. The test fails on the "
            "buggy revision as a consequence. Do NOT assert that the defect is "
            "present (no 'the value is huge here', no 'confirm the failure mode'): "
            "such an assertion holds on buggy and breaks on fixed, which makes the "
            "test PASS on buggy and FAIL on fixed — inverted, and worthless. If a "
            "verdict comes back P2F, that is what you did."]
        parts += [
            "KEEP IT DETERMINISTIC AND SMALL: one or two concrete inputs named "
            "by the evidence, no random sampling, no statistical comparison over "
            "many draws, no timing assertions. A test that draws 20 million "
            "samples to compare means is slow, flaky, and fails on both "
            "revisions — which scores the same as having no test."]
        force = os.environ.get("NUMBUGBENCH_INPUT_RULE", "").strip().lower()
        if force == "verbatim" or _has_concrete_input(context):
            parts += [
                "INPUT CHOICE: the context names a concrete input — take it from "
                "`numerical_failure.input_output_examples` or "
                "`trigger_conditions` and use it VERBATIM, with the same "
                "magnitude, dtype, shape and keyword arguments. These defects "
                "live at particular values: 1e-30 rounded to 1e-3, or float32 "
                "left at the float64 default, lands on a code path the fix never "
                "touched and the test then passes on both revisions.",
                "If the context gives an expected output for that input, assert "
                "THAT value. Derive one yourself only when the evidence gives "
                "none, and record in your assumptions that you did."]
            kinds = _value_kinds(context)
            if kinds:
                parts += [
                    "WHAT KIND OF VALUE THE ORACLE IS — the evidence's "
                    f"`value_kind` says {', '.join(sorted(kinds))}, and the "
                    "comparison must match it. A `string` is compared as TEXT: "
                    "a dtype or enum name such as \"NPY_CLONGDOUBLE\" is a "
                    "string in a lookup table, NOT a module attribute, so "
                    "`np.NPY_CLONGDOUBLE` does not exist and raises "
                    "AttributeError on both revisions. An `integer` or `count` "
                    "is compared exactly or with an inequality — an iteration "
                    "count such as `n_iter_` is a count, and no tolerance "
                    "applies to it. A `float` or `array` is compared with "
                    "`assert_allclose` at a tolerance. A `property` holds "
                    "without any single expected value, so assert the property "
                    "itself."]
        else:
            parts += [
                "INPUT CHOICE: the context names NO concrete input, so do not "
                "invent one and commit to it — a single guessed input usually "
                "lands on a path the fix never touched, and the test then passes "
                "on both revisions. Instead COVER the described condition: write "
                "the test over a small set of inputs that spans it (a few "
                "magnitudes, the relevant dtypes, both the default and the named "
                "keyword values), and assert the property that must hold for "
                "each. A handful of cases in one test is still deterministic and "
                "still small; it is a sweep, not random sampling.",
                "Better still, MEASURE first: make your first action "
                "`cd /tmp && python -c \"...\"` printing the result for several "
                "candidate inputs. The harness runs every such probe on BOTH "
                "revisions and shows you the pair, so you can see which input "
                "actually separates them before you write a single assertion. "
                "Spend an iteration on that rather than guessing."]
        parts += ["To create your test, set the action's `file` and `contents` "
                  "fields — do NOT write it with a shell heredoc. The harness "
                  "writes the file verbatim to BOTH revisions, so your test is "
                  "graded on the pair; a `command` that only describes writing a "
                  "file does nothing."]
    return "\n".join(parts)


def parse(raw: dict, task: str) -> Plan:
    if not isinstance(raw, dict) or not raw:
        return Plan(task=task, error="planner returned no object")
    return Plan(task=raw.get("task") or task,
                objective=raw.get("objective") or OBJECTIVES[task],
                evidence_used=list(raw.get("evidence_used") or []),
                assumptions=list(raw.get("assumptions") or []),
                actions=[a for a in (raw.get("actions") or []) if isinstance(a, dict)],
                success_criterion=raw.get("success_criterion") or "VALID_F2P")


def evidence_influence(plan: Plan, context) -> dict:
    def filled(path: str) -> bool:
        node = context.to_dict() if hasattr(context, "to_dict") else {}
        for part in path.split("."):
            if not isinstance(node, dict):
                return False
            node = node.get(part)
        return node not in (None, "", [], {})

    cited = list(plan.evidence_used)
    grounded = [p for p in cited if filled(p)]
    return {"cited": cited, "grounded": grounded,
            "ungrounded": [p for p in cited if p not in grounded],
            "n_cited": len(cited),
            "grounded_ratio": round(len(grounded) / len(cited), 3) if cited else 0.0}

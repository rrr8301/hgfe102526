from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .agent.llm.base import LLMClient, complete_json
from .repro_compare import finalize as _finalize_comparison
from .repro_specs import (patch_change_points, prelude_from_text, python_package,
                          test_case_pairs,
                          test_command_for,
                          test_files_of, test_prelude)
from .repro_tools import PURPOSES, registry
from .repro_workspace import BUGGY_DIR, FIXED_DIR

#: The execution tools a plan may name. compare_execution_results and
#: finalize_reproduction are deliberately not plannable — the orchestrator runs
#: them, so no plan can grade itself.
TOOLS = ("run_shell_command", "run_numerical_probe", "check_reported_examples")

#: What a planner believes one step will show. Checkable from the record, so a
#: mistaken belief is detectable rather than merely regrettable.
EXPECTATIONS = ("success", "test_fails", "test_passes",
                "pathology", "no_pathology", "any")

_SHELL_KEYS = ("command", "purpose", "timeout")
_PROBE_KEYS = ("code", "label", "expected", "rtol", "atol", "residual_tol", "timeout")
_EXAMPLE_KEYS = ("preamble", "timeout")

_ARG_PROPERTIES = {
    "command": {"type": "string", "description": "run_shell_command: the shell command"},
    "purpose": {"type": "string", "enum": PURPOSES,
                "description": "run_shell_command: use 'test' for the reproducing-test run"},
    "timeout": {"type": "integer", "description": "seconds"},
    "code": {"type": "string", "description": "run_numerical_probe: the probe source"},
    "label": {"type": "string",
              "description": "run_numerical_probe: the pairing label; use the SAME label "
                             "and the SAME code on both revisions"},
    "expected": {"type": "array", "items": {"type": "number"},
                 "description": "run_numerical_probe: reference values, if known"},
    "rtol": {"type": "number"},
    "atol": {"type": "number"},
    "residual_tol": {"type": "number"},
    "preamble": {"type": "string",
                 "description": "check_reported_examples: the imports and variable "
                                "bindings the reported expressions need (e.g. "
                                "\"from sympy import *\" plus z = Symbol('z')). The "
                                "expressions themselves come from the report."},
}


def step_schema(*, expect_description: str = "") -> dict:
    """The JSON Schema for one planned step.

    Pass `expect_description` to include the per-step expectation field; a
    strategy that verifies at a coarser granularity leaves it out so its planner
    is never asked for a prediction it will not be held to.
    """
    props = {
        "intent": {"type": "string", "description": "what this step establishes"},
        "tool": {"type": "string", "enum": list(TOOLS)},
        "revision": {"type": "string", "enum": ["buggy", "fixed"]},
        **_ARG_PROPERTIES,
    }
    required = ["intent", "tool"]
    if expect_description:
        props["expect"] = {"type": "string", "enum": list(EXPECTATIONS),
                           "description": expect_description}
        required.append("expect")
    return {"type": "object", "properties": props, "required": required}


@dataclass
class Step:
    intent: str
    tool: str
    revision: str
    expect: str = "any"
    arguments: dict = field(default_factory=dict)
    #: why the tool refused to run this step — a rejected probe produces no
    #: record, so this is all the trace (and the replanner) has to go on
    error: str = ""

    @property
    def working_dir(self) -> str:
        return FIXED_DIR if self.revision == "fixed" else BUGGY_DIR

    def tool_arguments(self) -> dict:
        return {**self.arguments, "working_dir": self.working_dir}

    def describe(self) -> str:
        what = (self.arguments.get("command") or self.arguments.get("label")
                or ("the reported examples" if self.tool == "check_reported_examples" else ""))
        return f"[{self.revision}] {self.tool}({str(what)[:80]}) — {self.intent}"


def steps_from_raw(raw_steps, default_revision: str = "buggy") -> list:
    """Build executable steps from a planner's JSON, dropping the unrunnable ones.

    A schema-constrained model still emits a step with no command, a probe with
    no code, or a tool that does not exist. Keeping those would resurface later
    as a mystery failure, so they are discarded here and the plan is judged on
    what remains.
    """
    steps: list[Step] = []
    for raw in raw_steps or []:
        if not isinstance(raw, dict) or raw.get("tool") not in TOOLS:
            continue
        tool = raw["tool"]
        keys = {"run_shell_command": _SHELL_KEYS,
                "check_reported_examples": _EXAMPLE_KEYS}.get(tool, _PROBE_KEYS)
        args = {k: raw[k] for k in keys if raw.get(k) not in (None, "", [], {})}
        if tool == "run_shell_command":
            if not args.get("command"):
                continue
            if args.get("purpose") not in PURPOSES:
                args.pop("purpose", None)
        elif tool == "run_numerical_probe" and not (args.get("code") and args.get("label")):
            continue                    # the examples step needs no arguments at all
        expect = raw.get("expect")
        steps.append(Step(
            intent=str(raw.get("intent") or "")[:300],
            tool=tool,
            revision="fixed" if raw.get("revision") == "fixed" else (
                default_revision if raw.get("revision") is None else "buggy"),
            expect=expect if expect in EXPECTATIONS else "any",
            arguments=args,
        ))
    return steps


def execute_step(ctx, step: Step) -> dict | None:
    """Run one planned step; return the stored record a verifier reads.

    The tool returns a trimmed dict for a model to read, but keeps the full
    record on the context — that is what gets judged, so that is what comes back.
    """
    result = registry.call(step.tool, ctx, step.tool_arguments())
    if isinstance(result, dict) and result.get("error"):
        step.error = str(result.get("error_brief") or result["error"])
        ctx.history.append({"step_error": result["error"], "step": step.describe()})
        return None
    store = ctx.results.get(step.revision) or {}
    if step.tool == "run_numerical_probe":
        return (store.get("probes") or {}).get(step.arguments.get("label"))
    if step.tool == "check_reported_examples":
        from .repro_tools import EXAMPLES_LABEL
        return (store.get("probes") or {}).get(EXAMPLES_LABEL)
    return store.get("last")


def summarize_step(step: Step, record: dict | None, note: str = "") -> str:
    """One line per executed step — the trace a planner or reflector reasons over."""
    if record is None:
        # the reason matters more than the fact: without it a replanner rewrites
        # the same rejected probe verbatim
        outcome = f"did not execute — {step.error}" if step.error else "did not execute"
    elif step.tool in ("run_numerical_probe", "check_reported_examples"):
        pathology = (record.get("pathology") or {}).get("present")
        outcome = (f"status={record.get('status')} pathology={pathology} "
                   f"values={record.get('values', [])[:4]}")
        if pathology is None:       # it never ran: the reason is the whole point
            reasons = (record.get("pathology") or {}).get("reasons") or []
            if reasons:
                outcome += f" — {reasons[0][:200]}"
    else:
        outcome = f"exit={record.get('exit_code')} ({record.get('runtime_s')}s)"
    return f"{step.describe()} -> {outcome}{note}"


# --------------------------------------------------------------------------- #
# the packaging decision (an orchestrator-invoked role, not a planned step)
# --------------------------------------------------------------------------- #
PACKAGER_SYSTEM = """\
You are NumReproAgent, deciding one thing about a reproduction attempt you have \
just finished: is this instance worth keeping as a benchmark image?

Packaging snapshots the container as <repo>-<issue_id>: both revisions exactly \
as they stand (built, with every dependency installed), the source and test \
patches, and every numerical probe that was run, replayable on either revision. \
The image's whole purpose is to let someone else see the defect on the buggy \
revision and its absence on the fixed one, offline.

Package when the evidence shows that separation — the reproducing test failing \
on buggy and passing on fixed, or a probe exhibiting the numerical defect on \
buggy and not on fixed. Do NOT package a run whose evidence does not separate \
the revisions, one where the probes never completed, or one whose defect is \
still visible on the fixed revision: an image of a reproduction that did not \
happen is worse than no image, because it looks like one that did.\
"""

PACKAGING_SCHEMA = {
    "type": "object",
    "properties": {
        "package": {"type": "boolean",
                    "description": "true to snapshot this instance as a benchmark image"},
        "notes": {"type": "string",
                  "description": "what this instance demonstrates — the buggy-vs-fixed "
                                 "evidence in one or two sentences. Kept in the image's "
                                 "metadata; it does not influence the reproduction status."},
    },
    "required": ["package", "notes"],
}


def build_packaging_request(spec, comparison: dict, report: dict) -> str:
    """The evidence the packaging decision is made on — the deterministic
    comparison, never the agent's own account of it."""
    numeric = (comparison or {}).get("numeric") or {}
    pairs = "\n".join(
        f"  - probe {p.get('label')!r}: {p.get('outcome')} "
        f"(defect on buggy={p.get('buggy')}, on fixed={p.get('fixed')})"
        for p in numeric.get("pairs") or []) or "  (no numerical probes were paired)"
    return "\n".join([
        f"ISSUE: {spec.repo} {spec.slug} ({spec.bug_type or 'unknown'})",
        f"IMAGE IF PACKAGED: <repo>-<issue_id> built from this container",
        "",
        "=== DETERMINISTIC COMPARISON ===",
        f"test outcome transition: {(comparison or {}).get('transition')}",
        f"fail_to_pass: {(comparison or {}).get('fail_to_pass')}",
        f"numerical oracle verdict: {numeric.get('verdict')}",
        pairs,
        "",
        f"reproduction status: {report.get('status')}",
        f"rationale: {report.get('rationale')}",
        "",
        "Decide: package this instance as a benchmark image, or not?",
    ])


def decide_packaging(ctx, client: LLMClient) -> dict:
    """Package the finished reproduction, once the agent has stopped.

    Called at the END of every reasoning loop, after the comparison and the
    verdict: the image is a snapshot of a settled result, never of a run still
    in progress. A REPRODUCED run is packaged on the spot; every other status is
    skipped (see :data:`repro_package.DEFAULT_STATUSES`). With
    `--no-auto-package` the model is asked first instead.

    A no-op — no LLM call, no cost — when the run was not started with packaging
    enabled, or when this run's status is one the caller excluded.
    """
    from .repro_package import image_name

    options = getattr(ctx, "package", None)
    if options is None or not options.enabled:
        return {}
    if ctx.comparison is None:
        registry.call("compare_execution_results", ctx, {})
    report = ctx.report or _finalize_comparison(ctx.comparison, ctx.spec)
    if not options.wants(report.get("status", "")):
        info = {"packaged": False, "image": "",
                "error": f"status {report.get('status')!r} is not packaged for this run"}
        ctx.history.append({"packager": {"asked": False, **info}})
        return info

    # REPRODUCED is not a judgement call. The verdict was already reached
    # deterministically — the test flipped and the oracle compared the two
    # revisions — so packaging it is bookkeeping, not a decision, and asking a
    # model for permission only adds a call that can answer "no" to an instance
    # that demonstrably reproduced. `--no-auto-package` (options.fallback=False)
    # is how a caller asks for the model's judgement instead.
    if options.fallback:
        ctx.emit("thought", f"PACKAGE: {report.get('status')} — packaging "
                            f"{image_name(ctx.spec, options)}")
        return registry.call("package_benchmark_image", ctx, {
            "notes": f"packaged automatically: {report.get('rationale', '')}"[:2000]})

    ctx.iterations += 1          # one LLM call, like ReAct's packaging turn
    raw = complete_json(client, PACKAGER_SYSTEM,
                        build_packaging_request(ctx.spec, ctx.comparison, report),
                        PACKAGING_SCHEMA, "submit_packaging_decision",
                        "Submit the packaging decision.")
    wants = bool(raw.get("package"))
    notes = str(raw.get("notes") or "")[:2000]
    ctx.history.append({"packager": {"asked": True, "package": wants, "notes": notes}})
    ctx.emit("thought", f"PACKAGE: {'yes' if wants else 'no'} — {notes}")
    if not wants:
        return {"packaged": False, "image": "", "error": "the agent declined to package",
                "notes": notes}
    return registry.call("package_benchmark_image", ctx, {"notes": notes})


# --------------------------------------------------------------------------- #
# an unusable plan is a diagnosable event, not a dead end
# --------------------------------------------------------------------------- #
#: How many times a planner may be asked again after answering with nothing
#: executable. Two is enough to clear a truncated or malformed answer; beyond
#: that the model is not going to plan this instance and the budget is better
#: spent reporting why.
MAX_PLAN_RETRIES = 2

#: How many times the example-sweep preamble may be corrected from the errors the
#: expressions raised. One is enough for the usual cause (a wrong import path, or
#: a wildcard import that shadows a library constant).
MAX_PROBE_REPAIRS = 2      # a described probe gets two chances to be corrected
MAX_PREAMBLE_RETRIES = 1


def unusable_plan_reason(raw: dict, stop_reason: str = "") -> str:
    """Why a planner's answer yielded nothing executable — the note a retry needs.

    An empty plan has three distinct causes, and re-sending the same request
    repeats whichever one happened: the answer was cut off at the output token
    limit, it was not readable as JSON at all, or every step it named was missing
    the fields that make a step runnable. Naming the cause is what lets the next
    attempt differ from the last, and it is what the run's error message should
    say instead of the bare fact that nothing came back.
    """
    if stop_reason == "max_tokens":
        return ("Your previous plan was CUT OFF at the output token limit before it was "
                "complete, so none of it could be executed. Answer again, shorter: fewer "
                "milestones, fewer steps each, and compact probe code — one probe that "
                "loops over a list of expressions instead of one step per expression.")
    if not raw:
        return ("Your previous answer could not be read as a plan. Answer by calling the "
                "submit tool with the plan object itself — no prose, no code fences.")
    return ("Every step in your previous plan was discarded as unrunnable. A "
            "run_shell_command step needs a non-empty `command`; a run_numerical_probe "
            "step needs both `code` and `label`. Give every step its tool and those fields.")


# --------------------------------------------------------------------------- #
# the report's own examples get evaluated even when the agent forgot
# --------------------------------------------------------------------------- #
PREAMBLE_SYSTEM = """\
You supply ONE thing: the setup code needed to evaluate a list of expressions \
taken from a bug report, inside a checked-out revision of the repository.

That means imports and variable bindings, in the revision's language, and \
nothing else — no expressions, no prints, no assertions. The expressions are \
given to you only so you can see what they need; a probe already evaluates them \
and records each result. Prefer the exact import paths that the run's own probes \
proved to work: a symbol that failed to import from the package root usually \
lives in a submodule. For a symbolic library, bind the free symbols the \
expressions use.

Import only from THIS repository and the packages it depends on. Any package \
named in these instructions is an illustration of the SHAPE of an answer, never \
something to import: a preamble that imports another project's library fails to \
load and the whole sweep is lost.

Set skip=true only when the expressions cannot be evaluated in this revision at \
all — never merely because the setup is awkward to write, and never because SOME \
of them are prose rather than code. Give the setup the evaluable ones need; the \
rest are recorded as unevaluable on both revisions, which is the right answer \
for them.\
"""

PREAMBLE_SCHEMA = {
    "type": "object",
    "properties": {
        "preamble": {"type": "string",
                     "description": "imports and bindings only, one per line"},
        "skip": {"type": "boolean",
                 "description": "true if these expressions cannot be evaluated here"},
    },
    "required": ["preamble"],
}


def _unevaluated_hints(ctx) -> str:
    """Why the examples were not observed — the concrete repair list.

    Two failures look alike in the counts and need opposite fixes: individual
    expressions raising (a binding is missing) and the probe never running at all
    (the preamble cannot load the module — an ESM/CJS mismatch, a wrong path).
    The second leaves NO per-example errors, so reporting only those hands the
    retry an empty page and it repeats the preamble that just failed.
    """
    from .repro_compare import EX_NOT_OBSERVED, compare_examples
    from .repro_tools import EXAMPLES_LABEL

    lines = []
    for rev in ("buggy", "fixed"):
        obs = ((ctx.results.get(rev) or {}).get("probes") or {}).get(EXAMPLES_LABEL)
        if not obs or obs.get("n_records"):
            continue
        tail = (obs.get("traceback_tail") or obs.get("stderr_tail") or "").strip()
        if tail:
            lines.append(f"On {rev} the example probe did not run at all "
                         f"({obs.get('status')}):\n    " + tail[-400:].replace("\n", "\n    "))
    current = compare_examples(ctx.results.get("buggy") or {},
                               ctx.results.get("fixed") or {}, ctx.spec)
    for row in current.get("examples", []):
        if row["outcome"] != EX_NOT_OBSERVED:
            continue
        # Both revisions failed, and they can fail for DIFFERENT reasons: the
        # buggy one because the function under test does not exist yet (which is
        # the bug), the fixed one because the preamble never bound a helper the
        # expression uses. Quoting only the first hides the repairable one.
        errors = {rev: row[rev].get("evaluation_error") for rev in ("buggy", "fixed")}
        detail = "; ".join(f"{rev} raised {err}" for rev, err in errors.items() if err)
        if detail:
            lines.append(f"  {row['input']}\n    {detail}")
    if not lines:
        return ""
    return ("These reported examples could NOT be evaluated — fix the preamble so "
            "they can be:\n" + "\n".join(lines[:8]))


_IMPORT_LINE = re.compile(r"^\+\s*(?:import\b|const\s+\w+\s*=\s*(?:require\(|await import\(|math\.)"
                          r"|^\+\s*from\s+\w)")


def _test_import_hints(spec) -> str:
    """How the reproducing test itself imports this package, and what it aliases.

    The test patch is ground truth for both — mathjs's own test opens with
    ``const big = math.bignumber``, which is exactly the binding the report's
    examples need and the one a model has no other way to guess.
    """
    path = Path(getattr(spec, "test_patch_path", "") or "")
    if not path.is_file():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if _IMPORT_LINE.match(line) and line[1:].strip() not in lines:
            lines.append(line[1:].strip())
        if len(lines) >= 12:
            break
    if not lines:
        return ""
    return ("The reproducing test imports and aliases these — the examples were "
            "written against them:\n  " + "\n  ".join(lines))


def _example_probe_hints(ctx) -> str:
    """What this run already learned about importing from these revisions."""
    lines = []
    for rev in ("buggy", "fixed"):
        for label, script in (ctx.probe_scripts.get(rev) or {}).items():
            if script.get("status") == "ok" and script.get("code"):
                imports = [ln for ln in str(script["code"]).splitlines()
                           if ln.strip().startswith(("import ", "from ", "const ", "require"))]
                if imports:
                    lines.append(f"imports that WORKED in probe {label!r}:\n  "
                                 + "\n  ".join(imports[:8]))
                    break
        if lines:
            break
    for obs in ((ctx.results.get("buggy") or {}).get("probes") or {}).values():
        for reason in (obs.get("pathology") or {}).get("reasons") or []:
            if "could not run" in reason:
                lines.append(f"an earlier probe failed with: {reason[:200]}")
                break
    return "\n".join(lines) or "(this run has no import evidence yet)"


def _trim(text, head: int = 300, tail: int = 300) -> str:
    """Keep both ends of a traceback.

    Keeping only the tail loses the line that says WHAT went wrong — a module
    resolution failure names the missing module on its first line and spends the
    rest on Node internals — which is how a fixable import error reaches the log
    as six frames of `node:internal/modules/esm/...` and nothing else.
    """
    text = str(text or "").strip()
    if len(text) <= head + tail:
        return text
    return f"{text[:head]}\n  … {len(text) - head - tail} chars elided …\n{text[-tail:]}"


def _module_style_hint(ctx, language: str) -> str:
    """For a JS/TS revision, state the module system instead of letting it be
    discovered by a failed probe."""
    from .repro_probe_langs import (JS_MODULE_STYLE_COMMAND, js_import_hint,
                                    js_module_style)
    from .repro_workspace import BUGGY_DIR

    if language not in ("javascript", "typescript"):
        return ""
    res = ctx.container.run(JS_MODULE_STYLE_COMMAND, workdir=BUGGY_DIR, timeout_s=60)
    return js_import_hint(js_module_style(res.stdout))


def _prelude_from_checkout(ctx, language: str) -> str:
    """The test file's imports, read from the revision itself.

    The patch-derived prelude is empty whenever a PR changed lines below the
    imports, which is most PRs. The checked-out file is right there in the
    container, so the imports are one `sed` away — and they are the ones that
    work, since they are what the test suite itself runs with.
    """
    from .repro_workspace import BUGGY_DIR

    for test_file in test_files_of(ctx.spec)[:3]:
        head = ctx.container.run(f"sed -n '1,80p' {BUGGY_DIR}/{test_file}",
                                 workdir="/", timeout_s=60)
        prelude = prelude_from_text(head.stdout, test_file)
        if prelude:
            return prelude
    return ""


def _run_examples(ctx, preamble: str, language: str, code: str = "") -> dict:
    """Evaluate the reported examples on both revisions.

    `code` is a generated probe (see :mod:`numagent.probe_codegen`); the SAME
    source runs on both revisions, which is what lets the comparator read the two
    sets of records as one experiment. Without it the harness builds the template
    probe around `preamble`, as before.
    """
    from .repro_tools import check_reported_examples
    from .repro_workspace import BUGGY_DIR, FIXED_DIR

    out = {}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        # a preamble means the caller wants the TEMPLATE probe: that is the
        # fallback sweep, and handing it the generated probe would re-run what
        # already came back short
        result = check_reported_examples(ctx, workdir, preamble=preamble,
                                         language=language,
                                         code=code or (_template_probe(ctx, language, preamble)
                                                       if preamble else ""))
        out[rev] = {k: result.get(k) for k in ("status", "checked", "recorded", "error")}
        out[rev]["could_not_evaluate"] = len(result.get("could_not_evaluate") or {})
        if not result.get("recorded"):
            out[rev]["traceback"] = _trim(result.get("traceback_tail"))
    return out


def _generate_example_probe(ctx, client, language: str, inputs: list) -> dict:
    """Have the model write the examples probe for this revision's language."""
    from .probe_codegen import generate_example_probe
    from .repro_workspace import BUGGY_DIR

    strategy = ctx.spec.strategy or {}
    examples = [(str(e.get("input", "")), str(e.get("buggy_output", "") or ""),
                 str(e.get("expected_output", "") or ""))
                for e in (strategy.get("input_output_examples") or [])
                if isinstance(e, dict) and str(e.get("input", "")).strip()]
    if not examples:
        return {}
    # What the two revisions are supposed to DO, which is what says which
    # quantity to record: an example can name the call while the difference
    # lives in its formatting, its dtype, or the exception it raises.
    behavior = {k: strategy.get(k, "") for k in
                ("summary", "expected_buggy_behavior", "expected_fixed_behavior",
                 "success_criterion")}
    hints = "\n".join(h for h in (_module_style_hint(ctx, language),
                                  _test_import_hints(ctx.spec),
                                  _example_probe_hints(ctx)) if h)
    ctx.iterations += 1
    result = generate_example_probe(
        client, language=language, examples=examples, behavior=behavior, hints=hints,
        on_attempt=lambda n, code: ctx.emit(
            "thought", f"EXAMPLE SWEEP — generated probe (attempt {n}):\n{code[:600]}"))
    if result.get("rejections"):
        ctx.history.append({"example_probe_generation": {
            "attempts": result.get("attempts"),
            "rejected": result["rejections"][:3],
            "ok": bool(result.get("code"))}})
    return result


#: Exception types that say the PROBE is wrong, not the revision. An example
#: that raises one of these identically on both revisions measured nothing: the
#: import does not exist, the name was never bound, the source does not parse.
#: A ValueError or an OverflowError is the opposite — that IS behaviour, and
#: often the very behaviour the report claims, so it is never "repaired" away.
_PROBE_AUTHORING_ERRORS = ("ImportError", "ModuleNotFoundError", "NameError",
                           "SyntaxError", "IndentationError", "AttributeError",
                           "UnboundLocalError",
                           # a wrong keyword or arity — "Unknown keyword
                           # arguments: ['k_factors']" on statsmodels#10214 —
                           # is the probe calling an API that does not exist in
                           # this revision. Safe to rewrite BECAUSE the error is
                           # identical on both revisions: an example that fails
                           # the same way on each discriminates nothing, so a
                           # rewrite can only add evidence, and a rewrite that
                           # observes less is discarded.
                           "TypeError")

#: how many times a probe that RAN and crashed is rewritten from its own errors
MAX_PROBE_REPAIRS = 2


def _example_errors(ctx) -> dict:
    """Examples that raised the SAME probe-authoring error on both revisions.

    Only both: an example that raises on buggy and answers on fixed is the
    report's own claim coming true, and rewriting the probe would erase it.
    """
    from .repro_probe_langs import example_error_text, is_example_error
    from .repro_tools import EXAMPLES_LABEL

    per_rev = []
    for rev in ("buggy", "fixed"):
        obs = ((ctx.results.get(rev) or {}).get("probes") or {}).get(EXAMPLES_LABEL) or {}
        per_rev.append({k: v for k, v in (obs.get("observed_records") or {}).items()
                        if is_example_error(v)})
    buggy, fixed = per_rev
    shared = {}
    for name, err in buggy.items():
        text = example_error_text(err)
        kind = text.split(":", 1)[0].strip()
        if name in fixed and kind in _PROBE_AUTHORING_ERRORS:
            shared[name] = text
    return shared


def _observed_examples(ctx, inputs: list) -> int:
    return len(inputs) - _still_missing(ctx)


def _repair_examples_probe(ctx, client, language: str, inputs: list) -> dict:
    """Rewrite a generated probe that crashed, from the errors it raised.

    Contract validation runs before execution and cannot know that
    ``from jax._src import xla`` is not importable in THIS revision; the run
    does. Feeding that back is the same loop the preamble path has always had,
    and it is the difference between an example recorded as an ImportError and
    one recorded as a value.
    """
    from .probe_codegen import generate_example_probe
    from .repro_workspace import BUGGY_DIR

    strategy = ctx.spec.strategy or {}
    examples = [(str(e.get("input", "")), str(e.get("buggy_output", "") or ""),
                 str(e.get("expected_output", "") or ""))
                for e in (strategy.get("input_output_examples") or [])
                if isinstance(e, dict) and str(e.get("input", "")).strip()]
    out = {}
    for _ in range(MAX_PROBE_REPAIRS):
        failures = _example_errors(ctx)
        if not failures:
            break
        before = _observed_examples(ctx, inputs)
        ctx.emit("thought", "EXAMPLE SWEEP — rewriting the probe; these examples "
                            "crashed the same way on BOTH revisions, so they measured "
                            "nothing:\n"
                            + "\n".join(f"  {n}\n    {e[:160]}"
                                         for n, e in list(failures.items())[:4]))
        ctx.iterations += 1
        result = generate_example_probe(
            client, language=language, examples=examples,
            behavior={k: strategy.get(k, "") for k in
                      ("summary", "expected_buggy_behavior", "expected_fixed_behavior",
                       "success_criterion")},
            hints=_example_probe_hints(ctx),
            feedback=("Your probe ran on both revisions. These examples raised — "
                      "the error is in the PROBE, not the revision, since both "
                      "revisions raised it identically. Rewrite so they record a "
                      "value: import only what you actually use, and reach the "
                      "quantity through the package's public API.\n"
                      + "\n".join(f"  {n}\n    raised {e[:200]}"
                                   for n, e in failures.items())))
        if not result.get("code"):
            ctx.history.append({"example_probe_repair": {"failed": result.get("error")}})
            break
        kept = {rev: dict(((ctx.results.get(rev) or {}).get("probes") or {}))
                for rev in ("buggy", "fixed")}
        ctx.example_probe = {"code": result["code"], "generated": True,
                             "attempts": result.get("attempts"), "repaired": True}
        out = _run_examples(ctx, "", language)
        after = _observed_examples(ctx, inputs)
        ctx.history.append({"example_probe_repair": {
            "examples_repaired": sorted(failures)[:6],
            "observed_before": before, "observed_after": after}})
        if after < before:
            # the rewrite lost ground; the earlier records are the better evidence
            for rev, probes in kept.items():
                ctx.store(rev)["probes"] = probes
            ctx.emit("thought", f"EXAMPLE SWEEP — the rewritten probe observed "
                                f"{after} example(s) where the previous one observed "
                                f"{before}; keeping the previous observations")
            break
    return out


def _template_probe(ctx, language: str, preamble: str) -> str:
    """The harness's own `eval` probe for these examples ("" if it cannot build one)."""
    from .repro_probe_langs import example_probe_source

    examples = [(str(e.get("input", "")), str(e.get("expected_output", "") or ""))
                for e in ((ctx.spec.strategy or {}).get("input_output_examples") or [])
                if isinstance(e, dict) and str(e.get("input", "")).strip()]
    try:
        return example_probe_source(language, examples, preamble)
    except ValueError:
        return ""


def _still_missing(ctx) -> int:
    from .repro_compare import compare_examples
    return compare_examples(ctx.results.get("buggy") or {},
                            ctx.results.get("fixed") or {}, ctx.spec).get("n_unobserved") or 0


def _parses_as_python(text: str) -> bool:
    import ast

    try:
        ast.parse(str(text or ""), mode="exec")
        return True
    except (SyntaxError, ValueError):
        return False


#: Aliases a bug report uses without ever saying what they are. `pd.Series(...)`
#: in a report means pandas to every reader; a preamble that omits the import
#: turns the example into `NameError: name 'pd' is not defined`, which reads as
#: an unobservable example rather than a missing line.
_CONVENTIONAL_ALIASES = {
    "np": "import numpy as np",
    "pd": "import pandas as pd",
    "plt": "import matplotlib.pyplot as plt",
    "sp": "import scipy as sp",
    "u": "",                      # too ambiguous to guess (astropy.units, unyt, …)
}


def _alias_imports(preamble: str, inputs) -> str:
    """Add the imports a report's shorthand assumes, when the preamble forgot them."""
    text = " ".join(str(i) for i in inputs)
    lines = []
    for alias, statement in _CONVENTIONAL_ALIASES.items():
        if not statement or not re.search(rf"\b{alias}\s*\.", text):
            continue
        if re.search(rf"(?m)^\s*(?:import|from)\b.*\b(?:as\s+{alias}|{alias}\s*$)",
                     preamble or "") or re.search(rf"(?m)^\s*{alias}\s*=", preamble or ""):
            continue
        lines.append(statement)
    return "\n".join(lines + [preamble.strip()]).strip() if lines else preamble


def _sanitize_preamble(text: str, language: str, fallback: str = "") -> str:
    """Keep only what can actually run as setup.

    A small model sometimes answers with a slab of the test file — a
    `@pytest.mark.parametrize` block, say — which is not a preamble and does not
    even parse. The probe contract then rejects the whole example sweep for a
    syntax error nobody wrote, and the report's values go unobserved. So the
    imports are salvaged and the rest is dropped.
    """
    text = str(text or "").strip()
    if not text or (language or "").lower() != "python" or _parses_as_python(text):
        return text
    salvaged = prelude_from_text(text)
    if salvaged and _parses_as_python(salvaged):
        return salvaged
    kept = "\n".join(line for line in text.splitlines()
                     if line.strip() and _parses_as_python(line))
    return kept if kept and _parses_as_python(kept) else fallback


def ensure_examples_observed(ctx, client: LLMClient) -> dict:
    """Evaluate the report's input/output examples on BOTH revisions if nothing has.

    The examples are the report's own claim about values, and an agent that gets
    a FAIL→PASS test can finish without ever checking them — leaving the instance
    reproduced at the level of the test and unobserved at the level of the values.
    For PYTHON the probe is GENERATED: the model writes it from the report's
    examples and from what the strategy says the two revisions do
    (:mod:`numagent.probe_codegen`), which lets an example be more than one
    evaluable expression. The record names stay the examples' own text, so the
    two revisions' observations pair up on their own. Everything else — and
    Python when generation fails — runs the older template probe, a fixed `eval`
    harness the model supplies only a preamble for.
    """
    from .probe_codegen import LANGUAGE as PROBE_CODEGEN_LANGUAGE
    from .repro_compare import EX_NOT_OBSERVED, compare_examples
    from .repro_probe_langs import EXAMPLE_LANGUAGES
    from .repro_probe_langs import is_example_error as _is_example_error
    from .repro_tools import EXAMPLES_LABEL, check_reported_examples
    from .repro_workspace import BUGGY_DIR, FIXED_DIR

    inputs = [str(e.get("input", "")) for e in
              ((ctx.spec.strategy or {}).get("input_output_examples") or [])
              if isinstance(e, dict) and str(e.get("input", "")).strip()]
    if not inputs:
        return {}
    current = compare_examples(ctx.results.get("buggy") or {},
                               ctx.results.get("fixed") or {}, ctx.spec)
    # Partly observed is the common case and the one that used to slip through: a
    # preamble that binds `bernoulli` but not `big` leaves half the report's
    # values unevaluated, and half the values checked is not the issue reproduced.
    if current.get("verdict") not in (None, EX_NOT_OBSERVED) and not current.get("n_unobserved"):
        return {"skipped": "every reported example was already observed"}

    language = ctx.probe_language(BUGGY_DIR)

    # GENERATED PROBE FIRST, for Python — the benchmark's target language, and
    # the one whose prompt can state the probe contract exactly. The model
    # writes the probe the way it writes every other one, which is what lets an
    # example be more than a single evaluable expression. Every other language
    # goes straight to the template path below, unchanged.
    # GENERATED PROBE FIRST, on Python. `check_reported_examples` writes it
    # itself (numagent.probe_codegen) and caches it on the context, so this runs
    # the same source the agent's own in-plan call would have run — and pays for
    # generation only if that call never happened.
    out = {}
    if language == PROBE_CODEGEN_LANGUAGE and ctx.llm is not None:
        out = _run_examples(ctx, "", language)
        # a probe that CRASHED on both revisions measured nothing about either;
        # rewrite it from its own errors before reading anything into the result
        out = _repair_examples_probe(ctx, client, language, inputs) or out
        observed = _observed_examples(ctx, inputs)
        if observed:
            # STOP HERE. Both probes record under the same label, so a later run
            # REPLACES the earlier one's observations — and the template probe,
            # which can only `eval` one expression per example, routinely records
            # something weaker than the generated probe did. On jax#40415 the
            # generated probe measured the tanh JVP asymmetry exactly (buggy
            # 1.49e-07 vs fixed 0.0) and the template that ran after it recorded
            # the input array on both revisions, turning a reproduced example
            # into "same on both". An example the generated probe could not
            # express is not reached by the template either: it goes to
            # ensure_described_examples, which uses its own label.
            if _still_missing(ctx):
                ctx.emit("thought", f"EXAMPLE SWEEP — the generated probe observed "
                                    f"{observed}/{len(inputs)} example(s); the rest are "
                                    f"left to the described-examples probe rather than "
                                    f"re-run with the template, which would overwrite "
                                    f"these records")
            ctx.history.append({"example_sweep": {**out, "preamble": "generated probe",
                                                  "observed": observed}})
            return out
        ctx.emit("thought", "EXAMPLE SWEEP — the generated probe observed nothing; "
                            "falling back to the template probe")

    if language not in EXAMPLE_LANGUAGES:
        return out or {"skipped": f"{language} cannot evaluate an expression given as text"}

    # Deterministic first: the reproducing test's own imports, rebased to the
    # repository root. When they work — and for a well-formed instance they
    # usually do — the model is never asked, and nothing is guessed.
    derived = test_prelude(ctx.spec, language) or _prelude_from_checkout(ctx, language)
    if derived:
        out = _run_examples(ctx, derived, language)
        if not _still_missing(ctx):
            ctx.history.append({"example_sweep": {**out, "preamble": "from the reproducing test"}})
            return out

    ctx.iterations += 1                # one call, and only when nothing observed
    raw = complete_json(
        client, PREAMBLE_SYSTEM,
        "\n".join([f"REPOSITORY: {ctx.spec.repo}   LANGUAGE: {language}",
                   f"REVISION ROOT: {BUGGY_DIR} (the same code is at {FIXED_DIR})",
                   "", "EXPRESSIONS TO BE EVALUATED:",
                   *(f"  - {i}" for i in inputs[:20]), "",
                   _module_style_hint(ctx, language), "",
                   _test_import_hints(ctx.spec), "", _example_probe_hints(ctx), "",
                   _unevaluated_hints(ctx), "",
                   "Give the imports and bindings these need."]),
        PREAMBLE_SCHEMA, "submit_preamble", "Submit the example preamble.")
    preamble = _alias_imports(
        _sanitize_preamble(str(raw.get("preamble") or ""), language, derived), inputs)
    if raw.get("skip") and not preamble.strip():
        # Declining is only final when there is nothing to run WITH. A report
        # usually mixes evaluable expressions with prose ones, and a model that
        # sees the prose ("round-trip the table and check the mask") declines for
        # the whole list — taking the concrete values down with the description.
        # With a preamble in hand the sweep runs, and the prose examples come
        # back as errors on both revisions, which is what unjudgeable means.
        ctx.history.append({"example_sweep": {"skipped": raw.get("preamble", "")[:200]}})
        return {"skipped": "the model declined and gave no usable preamble"}
    out = {}
    for attempt in range(MAX_PREAMBLE_RETRIES + 1):
        ctx.emit("thought", f"EXAMPLE SWEEP — preamble:\n{preamble[:400]}")
        out = _run_examples(ctx, preamble, language)
        failures = {}
        for rev in ("buggy", "fixed"):
            obs = ((ctx.results.get(rev) or {}).get("probes") or {}).get(EXAMPLES_LABEL) or {}
            failures.update({k: v for k, v in (obs.get("observed_records") or {}).items()
                             if _is_example_error(v)})
        if not (failures or _still_missing(ctx)) or attempt >= MAX_PREAMBLE_RETRIES:
            break
        # a preamble that binds the wrong thing is diagnosable from what the
        # expressions raised, and the fix is usually one import line — so the
        # error is handed back rather than recorded as "not observed"
        ctx.iterations += 1
        quoted = "\n".join(f"  {name}\n    raised {err}"
                            for name, err in list(failures.items())[:6])
        raw = complete_json(
            client, PREAMBLE_SYSTEM,
            "\n".join([f"REPOSITORY: {ctx.spec.repo}   LANGUAGE: {language}",
                        "", "Your previous preamble was:", preamble, "",
                        "With it, these expressions could not be evaluated:", quoted, "",
                        _unevaluated_hints(ctx), "", _test_import_hints(ctx.spec), "",
                        "Give a corrected preamble. Watch for names your imports shadow "
                        "— a wildcard import of a symbol module can rebind a library "
                        "constant to a plain symbol."]),
            PREAMBLE_SCHEMA, "submit_preamble", "Submit the corrected preamble.")
        if raw.get("skip") or not str(raw.get("preamble") or "").strip():
            break
        preamble = _alias_imports(
            _sanitize_preamble(str(raw["preamble"]), language, preamble), inputs)
        out["retried"] = attempt + 1
    ctx.history.append({"example_sweep": out})
    return out


# --------------------------------------------------------------------------- #
# the same procedure on both revisions, whether or not the agent got there
# --------------------------------------------------------------------------- #
#: How many recorded setup/build commands to replay before the test itself.
MAX_MIRRORED_COMMANDS = 8


DESCRIBED_SYSTEM = """\
Some of a bug report's examples are written as INSTRUCTIONS rather than as code \
— "set lon[0] to NaN; call mdl(lon, lat) for SphericalRotationSequence". They \
describe a real scenario that the repository can carry out; they simply cannot \
be handed to eval().

Write ONE probe, in the revision's language, that carries each of them out.

The rule that makes the result usable: record every result with the \
DESCRIPTION'S OWN TEXT as the record name, exactly as given to you, character \
for character. The comparator pairs the two revisions by that name, so a name \
you paraphrase is a result nobody can read. Record the value the description \
asks about — the returned array, the flag, the coordinate — not a summary of it.

Bind every name the scenario uses; the probe runs on its own, with nothing \
defined for it. Carry out only what the descriptions say: never substitute a \
value the report claims, and if one of them cannot be carried out in this \
revision, leave it out rather than inventing a result for it.

Every description must produce EXACTLY ONE record on every revision it runs on. \
If carrying one out raises, that IS its result on this revision — catch the \
exception and record it with probe_exception(text, e), which is available \
alongside probe(). Never let a scenario end with no record: a defect that shows \
up as an exception on the buggy revision and a value on the fixed one is the \
most common shape a report takes, and swallowing the exception erases exactly \
the half that proves it. Use probe_exception rather than formatting the error \
yourself — it records where the exception came from, which is what separates \
the revision misbehaving from your own probe calling something wrongly.

The harness defines exactly three functions and nothing else: probe(name, \
value), probe_iteration(**fields) and probe_exception(name, exception). There is \
no probe_result, no probe_value, no record — a call to any of those raises \
NameError and the run measures nothing.\
"""

DESCRIBED_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string",
                 "description": "the complete probe, in the revision's language"},
        "skip": {"type": "boolean",
                 "description": "true if none of these can be carried out here"},
    },
    "required": ["code"],
}

#: the label the described-example probe records under — kept apart from the
#: harness-generated sweep so a replay can tell which produced which value
DESCRIBED_LABEL = "described_examples"


def _unparseable_examples(ctx) -> list:
    """Reported examples the sweep could not even parse, on both revisions.

    Asked of the sweep's own output rather than of a parser, so it holds for
    every language whose harness reports what went wrong.
    """
    from .repro_probe_langs import is_example_error as _is_example_error
    from .repro_tools import EXAMPLES_LABEL
    from .repro_workspace import BUGGY_DIR

    def failed(rev: str) -> set:
        obs = ((ctx.results.get(rev) or {}).get("probes") or {}).get(EXAMPLES_LABEL) or {}
        return {name for name, value in (obs.get("observed_records") or {}).items()
                if _is_example_error(value)
                and "syntaxerror" in value.lower()}

    both = failed("buggy") & failed("fixed")
    inputs = [str(e.get("input", "")) for e in
              ((ctx.spec.strategy or {}).get("input_output_examples") or [])
              if isinstance(e, dict) and str(e.get("input", "")).strip()]
    if both:
        return [i for i in inputs if i in both]
    # The sweep is the best evidence, but it does not always get to run — a
    # preamble that fails to import leaves no records at all, and the prose
    # examples would then be invisible. Asking the text itself is the fallback.
    from .repro_compare import compare_examples
    from .repro_probe_langs import canonical, split_example_input

    if canonical(ctx.probe_language(BUGGY_DIR)) != "python":
        return []
    unobserved = set(compare_examples(ctx.results.get("buggy") or {},
                                      ctx.results.get("fixed") or {},
                                      ctx.spec).get("unobserved") or [])
    described = []
    for text in inputs:
        if unobserved and text not in unobserved:
            continue
        expression, _ = split_example_input(text, "python")
        try:
            ast.parse(expression, mode="eval")
        except (SyntaxError, ValueError):
            described.append(text)
    return described


#: Calls that a model reaches for out of habit and that no longer exist. numpy 2
#: removed a lot of long-deprecated aliases, and a probe written from memory
#: fails identically on both revisions — measuring nothing, twice.
_REMOVED_API = {
    "in1d": "np.isin", "alltrue": "np.all", "sometrue": "np.any",
    "float_": "np.float64", "complex_": "np.complex128", "unicode_": "np.str_",
    "object_": "object", "bool8": "np.bool_", "int0": "np.intp",
    "row_stack": "np.vstack", "product": "np.prod", "cumproduct": "np.cumprod",
}
_MISSING_ATTRIBUTE = re.compile(r"module '([\w.]+)' has no attribute '(\w+)'")

#: A probe told to use probe_exception sometimes formats the error itself
#: instead — "AttributeError(\"module 'numpy' …\")" — and a hand-written string
#: is indistinguishable from a measured value unless it is recognised as one.
_EXCEPTION_TEXT = re.compile(r"^\s*(?:raised:\s*)?[A-Za-z_]*(?:Error|Exception)\s*[(:]")


def _api_hint(problems) -> str:
    """Name the exact call that does not exist, and its replacement if known.

    "Use APIs that exist" is advice a model can agree with and then ignore;
    "numpy.in1d does not exist — use np.isin" is a correction it can apply.
    """
    lines = []
    for text in problems:
        for module, attribute in _MISSING_ATTRIBUTE.findall(str(text)):
            instead = _REMOVED_API.get(attribute)
            lines.append(f"  - {module}.{attribute} does not exist in the installed "
                         f"{module}" + (f" — use {instead} instead." if instead
                                        else " — find another way."))
    return ("\n".join(dict.fromkeys(lines)) if lines else "")


def ensure_described_examples(ctx, client: LLMClient) -> dict:
    """Carry out the reported examples that are prose, by writing a probe for them.

    The deterministic sweep evaluates an example as an expression, which is the
    right thing when the report gives one. When the report gives a procedure
    instead, that path can only record a syntax error — but the scenario is
    perfectly reproducible, and writing code for it is exactly what the agent is
    for. So the descriptions are handed back to the model as a probe-writing
    task, and the values it records pair with the same comparator.
    """
    from . import probe_contracts
    from .repro_probe_langs import canonical
    from .repro_tools import run_numerical_probe
    from .repro_workspace import BUGGY_DIR, FIXED_DIR

    described = _unparseable_examples(ctx)
    if not described:
        return {}
    language = canonical(ctx.probe_language(BUGGY_DIR))

    feedback, code = "", ""
    for attempt in range(MAX_PREAMBLE_RETRIES + 1):
        ctx.iterations += 1
        raw = complete_json(
            client, DESCRIBED_SYSTEM,
            "\n".join([f"REPOSITORY: {ctx.spec.repo}   LANGUAGE: {language}",
                        f"REVISION ROOT: {BUGGY_DIR} (the same code is at {FIXED_DIR})",
                        "", "DESCRIPTIONS TO CARRY OUT — record each result under the "
                        "exact text shown:",
                        *(f"  - {d}" for d in described[:8]), "",
                        _test_import_hints(ctx.spec), "",
                        (f"Your previous probe was rejected:\n{feedback}" if feedback else ""),
                        "", "Write the probe."]),
            DESCRIBED_SCHEMA, "submit_probe", "Submit the probe.")
        if raw.get("skip"):
            return {"skipped": "the model could not carry out these descriptions"}
        code = str(raw.get("code") or "").strip()
        if not code:
            return {"skipped": "the model returned no probe"}
        verdict = probe_contracts.validate_probe(language, code)
        if verdict.valid:
            break
        feedback = verdict.feedback(language)
        if attempt >= MAX_PREAMBLE_RETRIES:
            return {"skipped": "the probe did not meet the language's contract"}

    from .repro_probe_langs import EXAMPLE_ERROR
    from .repro_probe_langs import is_example_error as _is_example_error

    def record_if_raised(obs: dict) -> dict:
        """A scenario that RAISES has still been carried out — record the answer.

        The revision's answer to that description IS the exception, recorded
        under the description's own name so the comparator reads a revision-level
        error rather than silence. For many reports ("raises instead of
        returning a value"), that error is the claim being made.
        """
        # An observation carries `status`; the raw capture's `exception_type`
        # never survives into it.
        if not (obs.get("status") == "exception" and obs.get("target_reached")):
            return obs
        if obs.get("n_records"):
            return obs
        detail = [ln.strip() for ln in
                  str(obs.get("traceback_tail") or "").strip().splitlines() if ln.strip()]
        message = detail[-1] if detail else "raised"
        records = obs.setdefault("observed_records", {})
        for text in described:
            records.setdefault(text, f"{EXAMPLE_ERROR}{message[:200]}")
        obs["n_records"] = len(records)
        return obs

    def unusable(obs: dict) -> str:
        """Why this run tells us nothing about the revision — "" when it does.

        A probe that never reached the target, or that failed inside its OWN
        code, has measured nothing: `np.in1d` was removed in numpy 2.0, and a
        probe that calls it fails identically on both revisions. That is a probe
        to rewrite, not a result to record.
        """
        if obs.get("status") == "probe_generation_error":
            # rejected before it ran: the contract says exactly what is wrong
            return obs.get("error_brief") or "; ".join(obs.get("errors") or []) \
                or "the probe failed its contract"
        if obs.get("target_reached") is False:
            return _trim(obs.get("traceback_tail")) or "the probe never reached the target"
        records = (obs.get("observed_records") or {})
        if not records:
            return _trim(obs.get("traceback_tail")) or (
                "the probe ran to the end without calling probe(name, value) once — "
                "whatever it computed, nothing was recorded")
        for value in records.values():
            if isinstance(value, dict) and "in_repository" in value:
                if value.get("in_repository"):
                    return ""                  # the REVISION raised: that is an answer
                continue
            if isinstance(value, str) and (_is_example_error(value)
                                           or _EXCEPTION_TEXT.match(value)):
                continue                       # an error, hand-formatted or not
            return ""                          # a real value
        first = next(iter(records.values()))
        detail = (first.get("message") if isinstance(first, dict) else str(first))
        return f"every record came from the probe's own failure: {str(detail)[:200]}"

    out = {"n_described": len(described)}
    for repair in range(MAX_PROBE_REPAIRS + 1):
        ctx.emit("thought", f"DESCRIBED EXAMPLES — {len(described)} of the report's examples "
                            f"are instructions rather than expressions; running a probe "
                            f"written for them:\n{code[:400]}")
        observations = {}
        for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
            obs = record_if_raised(run_numerical_probe(ctx, code=code, working_dir=workdir,
                                                       label=DESCRIBED_LABEL))
            observations[rev] = obs
            out[rev] = {k: obs.get(k) for k in ("status", "n_records", "target_reached")}
        broken = {rev: why for rev, obs in observations.items() if (why := unusable(obs))}
        # A revision that cannot import the package under test fails every probe
        # the same way: a rewrite spends a call and changes nothing. Stop, and
        # let the verdict name it as the setup problem it is.
        unimportable = [rev for rev in broken
                        if observations[rev].get("status") == "environment_error"]
        if unimportable:
            ctx.emit("thought", "the package under test is not importable on "
                                f"{', '.join(unimportable)} — no probe rewrite can fix "
                                "that, so the probe is not repaired")
            out["environment_error"] = unimportable
        if not broken or repair >= MAX_PROBE_REPAIRS or unimportable:
            out["unusable"] = broken
            break
        # The probe's own failure is diagnosable from what it printed, and the
        # fix is usually one call — so it is handed back rather than recorded as
        # a revision that could not be measured.
        ctx.iterations += 1
        ctx.emit("thought", "the probe failed in its OWN code — repairing it:\n  "
                            + "\n  ".join(f"[{rev}] {why[:300]}" for rev, why in broken.items()))
        request = "\n".join([
            f"REPOSITORY: {ctx.spec.repo}   LANGUAGE: {language}", "",
            "Your probe was:", code, "",
            "It did not measure the revision. What went wrong:",
            *(f"  [{rev}] {why}" for rev, why in broken.items()), "",
            _api_hint(broken.values()), "",
            "Rewrite it so every description records a result that comes from "
            "the repository under test. Call only what the description names "
            "plus what those calls need — every extra helper is another way "
            "to fail without measuring anything — and reach that operation "
            "before recording.", "",
            _test_import_hints(ctx.spec)])
        # A rewrite that fails the contract gets ONE more try with the reason,
        # rather than ending the repair on a mistake the contract already named.
        stopped, fixed_code = "", ""
        for attempt in range(2):
            raw = complete_json(client, DESCRIBED_SYSTEM, request, DESCRIBED_SCHEMA,
                                "submit_probe", "Submit the corrected probe.")
            fixed_code = str(raw.get("code") or "").strip()
            if raw.get("skip"):
                stopped = "the model declined to rewrite it (skip)"
                break
            if not fixed_code:
                stopped = "the model returned no code"
                break
            verdict = probe_contracts.validate_probe(language, fixed_code)
            if verdict.valid:
                stopped = ""
                break
            stopped = "the rewrite failed the probe contract: " + "; ".join(verdict.errors)
            request += ("\n\nYour rewrite was rejected before it ran:\n  "
                        + "\n  ".join(verdict.errors) + "\nFix exactly that.")
        if stopped:
            # said, not swallowed: a repair that ends here leaves the example
            # unchecked, and the log is the only place anyone learns why
            ctx.emit("thought", f"probe repair abandoned — {stopped[:300]}")
            out["unusable"], out["repair_stopped"] = broken, stopped[:500]
            break
        code, out["repaired"] = fixed_code, repair + 1
    ctx.history.append({"described_examples": out})
    return out


def ensure_test_on_both_revisions(ctx) -> dict:
    """Replay the recorded test procedure on the revision that never got one.

    A reproduction is a COMPARISON, and half of it is worthless: a run that
    exercised only the buggy revision grades as fail->missing no matter how
    clearly the bug showed. Agents lose the second half routinely — to a budget
    spent repairing the first, to a plan that never reached it, to a milestone
    abandoned mid-way — and the fix needs no reasoning, because the procedure
    that worked on one revision is already recorded: the successful setup and
    build commands, then the test, with the paths swapped.

    Deterministic and idempotent: it runs only when exactly one side has a test
    result, and it never overwrites one.
    """
    from .repro_tools import run_shell_command
    from .repro_workspace import BUGGY_DIR, FIXED_DIR

    have = {rev: (ctx.results.get(rev) or {}).get("test") for rev in ("buggy", "fixed")}
    if bool(have["buggy"]) == bool(have["fixed"]):
        return {}                       # both ran, or neither did — nothing to mirror
    src = "buggy" if have["buggy"] else "fixed"
    dst = "fixed" if src == "buggy" else "buggy"
    src_dir, dst_dir = ((BUGGY_DIR, FIXED_DIR) if src == "buggy" else (FIXED_DIR, BUGGY_DIR))
    swap = lambda cmd: str(cmd or "").replace(src_dir, dst_dir)   # noqa: E731

    prepared = []
    for record in (ctx.results.get(src) or {}).get("commands", []):
        if record.get("purpose") not in ("setup", "build") or record.get("exit_code") != 0:
            continue
        command = swap(record.get("command"))
        if command and command not in prepared:
            prepared.append(command)
    prepared = prepared[:MAX_MIRRORED_COMMANDS]

    ctx.emit("thought", f"MIRRORING the recorded procedure onto {dst}: "
                        f"{len(prepared)} setup/build command(s), then the test")
    for command in prepared:
        run_shell_command(ctx, command, working_dir=dst_dir, purpose="setup")
    test_command = swap((have[src] or {}).get("command"))
    result = run_shell_command(ctx, test_command, working_dir=dst_dir, purpose="test")
    out = {"mirrored_from": src, "onto": dst, "setup_commands": len(prepared),
           "test_command": test_command, "exit_code": result.get("exit_code")}
    ctx.history.append({"mirrored_procedure": out})
    return out


def _rust_package_of(ctx, test_file: str) -> str:
    """The crate that owns `test_file` — walk up to its Cargo.toml and read the name."""
    from .repro_workspace import BUGGY_DIR

    directory = test_file
    for _ in range(6):
        directory = directory.rsplit("/", 1)[0] if "/" in directory else ""
        if not directory:
            break
        script = (r"""sed -n 's/^name[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' """
                  + f"{BUGGY_DIR}/{directory}/Cargo.toml 2>/dev/null | head -1")
        res = ctx.container.run(script, workdir="/", timeout_s=60)
        name = (res.stdout or "").strip()
        if name:
            return name
    return ""


#: crates whose backend needs a native toolkit (libtorch, a GPU driver, CUDA) —
#: last resort when several crates can host the same generated tests.
_NATIVE_BACKEND = re.compile(r"\btch\b|wgpu|cuda|metal|-sys\b|opencl", re.I)


def _rust_testgen_hosts(ctx, defining_crate: str) -> list:
    """Crates that INSTANTIATE `defining_crate`'s generated tests, cheapest first.

    A Rust crate can define its tests as macro templates and leave the running
    of them to whoever calls the macro: burn-autodiff's `#[testgen(ad_sigmoid)]`
    only becomes a real test inside burn-ndarray, burn-tch and burn-wgpu, which
    each expand `burn_autodiff::testgen_all!()`. `cargo test -p burn-autodiff`
    then reports "running 0 tests" and exits 0 — a pass that tested nothing.

    CPU backends are tried before the ones that need libtorch or a GPU: they are
    the only ones that can be expected to build in a headless container.
    """
    from .repro_workspace import BUGGY_DIR

    macro = f"{defining_crate.replace('-', '_')}::testgen"
    # The crate a file belongs to is the nearest directory ABOVE it holding a
    # Cargo.toml — not the first path segment, which is `crates/` in every
    # workspace that groups its members in a subdirectory.
    walk_up = (
        f"for f in $(grep -rl --include=*.rs {shlex.quote(macro)} {BUGGY_DIR} "
        f"2>/dev/null | head -20); do d=$(dirname \"$f\"); "
        f"while [ \"$d\" != \"/\" ] && [ \"$d\" != \"{BUGGY_DIR}\" ]; do "
        f"if [ -f \"$d/Cargo.toml\" ]; then echo \"$d\"; break; fi; "
        f"d=$(dirname \"$d\"); done; done | sort -u")
    found = ctx.container.run(walk_up, workdir="/", timeout_s=180)
    directories = [d for d in (found.stdout or "").split() if d.startswith(BUGGY_DIR)]
    if not directories:
        return []

    manifests = ctx.container.run(
        "for d in " + " ".join(shlex.quote(d) for d in directories)
        + "; do echo \"@@$d\"; cat \"$d/Cargo.toml\" 2>/dev/null; done",
        workdir="/", timeout_s=120)
    crates, current = [], None
    for line in (manifests.stdout or "").splitlines():
        if line.startswith("@@"):
            current = {"dir": line[2:].strip(), "name": "", "native": False}
            crates.append(current)
        elif current is not None:
            name = re.match(r'^name\s*=\s*"([^"]+)"', line.strip())
            if name and not current["name"]:
                current["name"] = name.group(1)
            elif _NATIVE_BACKEND.search(line):
                current["native"] = True
    hosts = [c for c in crates if c["name"] and c["name"] != defining_crate]
    return ([c["name"] for c in hosts if not c["native"]]
            + [c["name"] for c in hosts if c["native"]])


#: A skip reason names what the test wanted: "requires pandas", "requires
#: narwhals and polars", "could not import 'pandas'". pytest is asked for these
#: (`-rs`) precisely so the answer does not have to be guessed.
#: greedy to the LAST colon: the line carries a file path with its own colons
#: ("test_df.py:457: requires pandas"), and the reason is what follows them all
_SKIP_LINE = re.compile(r"^\s*SKIPPED\b[^\n]*:[ \t]*\n?[ \t]*(\S[^\n]*?)\s*$", re.M)
_IMPORT_NAMED = re.compile(r"could not import ['\"]?([\w.]+)|no module named ['\"]?([\w.]+)"
                           r"|\b([A-Za-z_][\w.]*) (?:is )?not (?:available|installed|found)", re.I)
#: import name -> the distribution that provides it, where the two differ; a
#: skip names what failed to IMPORT, and `pip install pylab` is a different project
_DIST_OF = {"pylab": "matplotlib", "mpl_toolkits": "matplotlib", "yaml": "PyYAML",
            "cv2": "opencv-python-headless", "sklearn": "scikit-learn", "PIL": "pillow",
            "pil": "pillow", "skimage": "scikit-image", "bs4": "beautifulsoup4",
            "attr": "attrs", "dateutil": "python-dateutil", "google.protobuf": "protobuf",
            "gmpy": "gmpy2", "Crypto": "pycryptodome", "serial": "pyserial"}
_REQUIRES = re.compile(r"^requires?\s+(.+?)(?:\s+(?:to be )?installed)?$", re.I)

#: words a skip reason uses that are not packages
_NOT_A_PACKAGE = {"a", "an", "the", "and", "or", "internet", "network", "display",
                  "root", "remote", "data", "access", "connection", "hypothesis",
                  "this", "test", "tests", "python", "os", "platform", "linux",
                  "windows", "macos", "gpu", "cuda"}


def _missing_from_skips(output: str, limit: int = 4) -> list:
    """Packages a fully-skipped run says it needed, most-wanted first.

    A parametrised test skips once per backend — pandas, polars, pyarrow, dask —
    and installing all of them to reach one variant is a lot of wheels for
    nothing. What the FIRST reason names, plus whatever more than one reason
    names, is the part the case actually turns on.
    """
    order, counts = [], {}
    for reason in _SKIP_LINE.findall(output or ""):
        named = []
        found = _IMPORT_NAMED.search(reason)
        if found:
            named = [g for g in found.groups() if g]
        else:
            m = _REQUIRES.match(reason.strip())
            if m:
                named = re.split(r"\s*(?:,|\band\b|\bor\b)\s*", m.group(1))
        for raw in named:
            raw = raw.strip().strip("'\"")
            name = _DIST_OF.get(raw, _DIST_OF.get(raw.split(".")[0], raw)).lower()
            if (not re.fullmatch(r"[a-z][\w.-]{2,}", name) or name in _NOT_A_PACKAGE):
                continue
            counts[name] = counts.get(name, 0) + 1
            if name not in order:
                order.append(name)
    if not order:
        return []
    first_line_names = {n for n in order[:1]}
    wanted = [n for n in order if n in first_line_names or counts[n] > 1]
    return (wanted or order)[:limit]


def _install_missing(ctx, modules) -> list:
    """Install optional dependencies into BOTH revisions, however they are set up.

    A revision that was built has its own venv and must be told separately; one
    that runs from source shares the image's interpreter, so a single install
    serves both.
    """
    from .repro_workspace import BUGGY_DIR, FIXED_DIR

    wanted = " ".join(shlex.quote(m) for m in modules)
    done = []
    for workdir in (BUGGY_DIR, FIXED_DIR):
        has_venv = ctx.container.run(f"test -x {workdir}/.venv/bin/pip && echo yes",
                                     workdir="/", timeout_s=60)
        pip = (f"{workdir}/.venv/bin/pip" if "yes" in (has_venv.stdout or "") else "pip")
        r = ctx.container.run(f"{pip} install --no-cache-dir {wanted}",
                              workdir=workdir, timeout_s=1800)
        done.append({"revision": workdir, "pip": pip, "ok": r.exit_code == 0})
        if pip == "pip":
            break                       # one shared interpreter, one install
    return done


_TEST_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+(test\w*)\s*\(")


def _enclosing_tests(ctx) -> list:
    """The tests a patch changes from INSIDE, read from the patched file.

    The hunk header names the nearest enclosing definition — for a change to a
    nested helper that is the helper (`def kernel`, `def impl`), not the test
    around it, which can sit well outside the hunk's few lines of context. The
    patched file is in the revision, so the enclosing `def test…` is read from
    it: walking up past anything indented deeper than the change, or — for a
    change to a decorator's parameters, which sits ABOVE its test — down to
    the next test within a few lines.
    """
    from .repro_workspace import BUGGY_DIR

    pairs, cache = [], {}
    for test_file, line in patch_change_points(ctx.spec):
        if test_file not in cache:
            text = ctx.container.run(f"cat {shlex.quote(test_file)} 2>/dev/null",
                                     workdir=BUGGY_DIR, timeout_s=60).stdout or ""
            cache[test_file] = text.splitlines()
        lines = cache[test_file]
        if not 0 < line <= len(lines):
            continue
        changed = lines[line - 1]
        indent = len(changed) - len(changed.lstrip())
        found = None
        for i in range(line - 1, -1, -1):
            m = _TEST_DEF.match(lines[i])
            if m and len(m.group(1)) < indent:
                found = m.group(2)
                break
            stripped = lines[i].lstrip()
            if stripped and len(lines[i]) - len(stripped) == 0 and i < line - 1:
                break                           # left the enclosing top-level block
        if found is None:
            for i in range(line - 1, min(line + 25, len(lines))):
                m = _TEST_DEF.match(lines[i])
                if m:
                    found = m.group(2)
                    break
        if found and (test_file, found) not in pairs:
            pairs.append((test_file, found))
    return pairs


def ensure_reproducing_test_ran(ctx) -> dict:
    """Run the test the PATCH adds, when nothing that ran actually named it.

    The mined selector describes the project's CI. If it targets another suite,
    both revisions pass it and the instance is graded NOT_REPRODUCED with a
    reproducing test that was applied and never executed. The patch names the
    case; the language decides the command; neither needs a model.
    """
    from .repro_tools import run_shell_command
    from .repro_workspace import BUGGY_DIR, FIXED_DIR

    # added cases first — they exist only for this bug — then the existing ones
    # the patch tightened, which is how many fixes arrive with no new test at all
    #
    # Each case runs from the file that DEFINES it. Pairing every case with the
    # patch's first test file ran astropy 16005's cases against a file that has
    # neither, which selects nothing on both revisions — "no tests", and an
    # INVALID_STRATEGY for an instance whose test was never actually tried.
    pairs = test_case_pairs(ctx.spec) or _enclosing_tests(ctx)
    cases = [case for _, case in pairs]
    if not pairs:
        return {}
    from .repro_tools import _runs_tests

    executed = [str((rec or {}).get("command") or "")
                for rev in ("buggy", "fixed")
                for rec in (ctx.results.get(rev) or {}).get("commands", [])
                if (rec or {}).get("purpose") == "test"
                and _runs_tests(str((rec or {}).get("command") or ""),
                                str((rec or {}).get("output") or ""))]
    # Naming the case is not running it. An agent that filtered for the case in
    # the WRONG package gets "0 tests, exit 1" — the case was named, the runner
    # ran, and nothing was tested. So this steps aside only when the recorded
    # outcome is definite (a pass or a fail) on both revisions AND some runner
    # named the case; anything else means the reproducing test still has not run.
    from .repro_compare import parse_test_outcome

    outcomes = {rev: parse_test_outcome((ctx.results.get(rev) or {}).get("test"))["status"]
                for rev in ("buggy", "fixed")}
    decisive = all(status in ("pass", "fail") for status in outcomes.values())
    named = any(case in command for case in cases for command in executed)
    if decisive and named:
        return {}

    language = getattr(ctx.spec, "language", "") or "python"

    runners: dict = {}

    def runner_for(test_file: str) -> str:
        """The runner this file needs, asked once per file."""
        if language in ("c", "cpp"):
            # only when the harness actually configured the project: without a
            # build directory there is nothing for ctest to run
            return "ctest" if ((ctx.workspace_info or {}).get("cmake") or {}).get(
                "configured") else ""
        if language not in ("javascript", "typescript"):
            return ""
        if test_file not in runners:
            from .repro_workspace import js_test_runner
            runners[test_file] = js_test_runner(ctx.container, BUGGY_DIR, test_file)
        return runners[test_file]

    def package_for(test_file: str) -> str:
        if language == "rust":
            return _rust_package_of(ctx, test_file)
        # python: the import name, so a built package's test is addressed from it
        return python_package(ctx.spec) if language == "python" else ""

    if not test_command_for(language, pairs[0][0], pairs[0][1], package_for(pairs[0][0]),
                            installed=getattr(ctx.spec, "needs_build", False),
                            sanitizer=getattr(ctx.spec, "sanitizer", ""),
                            runner=runner_for(pairs[0][0])):
        return {"skipped": f"no test command shape for {language}"}

    def run_one(cmd: str, rev: str) -> dict:
        """Run `cmd` on one revision and return the STORED record.

        The tool's return value is the trimmed view a model reads — it carries no
        combined `output`, so judging that would call every run a pass.
        """
        workdir = BUGGY_DIR if rev == "buggy" else FIXED_DIR
        run_shell_command(ctx, cmd, working_dir=workdir, purpose="test")
        return (ctx.results.get(rev) or {}).get("last") or {}

    def status_of(record: dict) -> str:
        return parse_test_outcome(record)["status"]

    ctx.emit("thought", f"RUNNING THE PATCH'S OWN TEST on both revisions — nothing that ran "
                        f"named {', '.join(repr(c) for c in cases)}")
    hosts, tried, attempts, out = {}, [], [], {}          # hosts: package -> crates
    tried_install = False
    # A patch usually adds MORE than one case, and only some of them are about
    # the defect: burn 1140 adds should_diff_sigmoid (a value check that passes
    # either way) alongside small_neg_val_should_not_cause_grad_overflow (the
    # overflow the fix is for). Running only the first would grade a perfectly
    # good instance NOT_REPRODUCED, so each is tried until one TRANSITIONS.
    for test_file, case in pairs[:8]:
        package = package_for(test_file)
        command = test_command_for(language, test_file, case, package,
                                   installed=getattr(ctx.spec, "needs_build", False),
                                   sanitizer=getattr(ctx.spec, "sanitizer", ""),
                                   runner=runner_for(test_file))
        buggy_record = run_one(command, "buggy")
        # "0 tests ran" is not an answer. When the crate that DEFINES the test
        # only templates it, the run has to move to where it is instantiated.
        if language == "rust" and status_of(buggy_record) == "no_tests":
            if package not in hosts:
                hosts[package] = _rust_testgen_hosts(ctx, package)
            for host in hosts[package][:2]:
                retry = test_command_for(language, test_file, case, host)
                tried.append(retry)
                ctx.emit("thought", f"{package!r} only TEMPLATES {case!r} — running it "
                                    f"where it is instantiated:\n    {retry}")
                buggy_record = run_one(retry, "buggy")
                if status_of(buggy_record) != "no_tests":
                    command, package = retry, host
                    break
        fixed_record = run_one(command, "fixed")
        buggy, fixed = status_of(buggy_record), status_of(fixed_record)
        # A skipped test is not an answer: it asked for an optional dependency the
        # image does not have, and said which. EITHER side being skipped is
        # enough to act on — the revisions must be judged on equal footing, and
        # one of them silently testing nothing while the other really runs is
        # exactly how a false verdict gets recorded.
        skipped = [r for r in (buggy_record, fixed_record)
                   if parse_test_outcome(r)["failure_kind"] == "skipped"]
        if skipped and not tried_install:
            names = _missing_from_skips("\n".join(r.get("output") or "" for r in skipped))
            if names:
                tried_install = True
                ctx.emit("thought", f"a revision SKIPPED every selected test for a missing "
                                    f"optional dependency — installing {names} on both and "
                                    f"re-running")
                out["installed_for_tests"] = _install_missing(ctx, names)
                buggy_record = run_one(command, "buggy")
                fixed_record = run_one(command, "fixed")
                buggy, fixed = status_of(buggy_record), status_of(fixed_record)
        attempts.append({"case": case, "command": command,
                         "buggy": buggy, "fixed": fixed})
        out = {"case": case, "command": command, "package": package,
               "statuses": {"buggy": buggy, "fixed": fixed},
               "exit_codes": {"buggy": buggy_record.get("exit_code"),
                              "fixed": fixed_record.get("exit_code")},
               "also_tried": tried, "attempts": attempts}
        # an empty case is a whole-file target — say which file, not ''
        shown = repr(case) if case else f"the whole of {test_file}"
        # A hang or crash on buggy with a pass on fixed exposes the bug too (the same rule
        # compare_execution_results applies): numba#9747's binomial loops forever on buggy,
        # and moving on to the next case graded an unrelated one pass->pass.
        crashed = parse_test_outcome(buggy_record).get("failure_kind") == "crash"
        if fixed == "pass" and (buggy in ("fail", "timeout") or crashed):
            how = "FAILS" if buggy == "fail" else "HANGS" if buggy == "timeout" else "CRASHES"
            ctx.emit("thought", f"{shown} is the case that exposes the bug: it {how} on "
                                f"buggy and PASSES on fixed.")
            break
        if len(cases) > 1:
            ctx.emit("thought", f"{shown} is {buggy} on buggy and {fixed} on fixed — it "
                                f"does not discriminate; trying the next case the patch adds.")
    ctx.history.append({"reproducing_test_run": out})
    return out

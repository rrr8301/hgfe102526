from __future__ import annotations

import json

from ..planning import step_schema
from ..repro_specs import (ReproSpec, test_cases_added, test_files_of,
                           environment_block, test_prelude_block)

# --------------------------------------------------------------------------- #
# schemas
# --------------------------------------------------------------------------- #
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "description": "one sentence: what this plan establishes"},
        "oracle": {"type": "string",
                   "description": "how the buggy→fixed difference will be visible in these steps"},
        "steps": {
            "type": "array",
            "description": "the procedure, in execution order, for BOTH revisions",
            "items": step_schema(expect_description=(
                "what you believe this step will show. Be honest: the reproducing test is "
                "expected to FAIL on buggy (test_fails) and PASS on fixed (test_passes). A "
                "wrong expectation here is what triggers reflection.")),
        },
    },
    "required": ["goal", "steps"],
}

#: Exactly the reflector contract: what went wrong, whether the plan must change,
#: and — in prose — the steps that replace the rest of it.
REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "failure_cause": {
            "type": "string",
            "description": "short snake_case cause, e.g. regression_test_added_by_fix, "
                           "missing_dependency, system_package, stale_selector, build_error, "
                           "test_not_sensitive_to_bug, probe_measured_wrong_thing, "
                           "expectation_was_wrong",
        },
        "plan_change_required": {
            "type": "boolean",
            "description": "false when the observation is actually consistent with reproducing "
                           "the bug (a reproducing test SHOULD fail on the buggy revision) — the "
                           "plan then continues unchanged",
        },
        "new_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "prose steps that REPLACE the remainder of the plan, in order. Empty "
                           "when plan_change_required is false.",
        },
    },
    "required": ["failure_cause", "plan_change_required", "new_steps"],
}


# --------------------------------------------------------------------------- #
# planner
# --------------------------------------------------------------------------- #
_WORKSPACE = """\
The workspace is ALREADY prepared inside a Docker container — nothing clones, \
checks out, or explores the repository:
  - /workspace/buggy — the buggy commit, with the reproducing test applied \
(the test is present and is expected to FAIL here);
  - /workspace/fixed — the fixed commit as-is (already contains the test + the \
fix; the same test is expected to PASS here).
Installed packages persist between steps, and a step naming revision "buggy" \
runs in /workspace/buggy."""

PLANNER_SYSTEM = f"""\
You are the PLANNER of NumReproAgent, which REPRODUCES a reported numerical bug \
by showing a behavior difference between two prepared revisions.

{_WORKSPACE}

You do not execute anything. You commit to the WHOLE procedure up front, as an \
ordered list of steps, and each step declares what you expect it to show. Three \
tools are plannable:
  - run_shell_command(command, purpose, timeout): install dependencies, build, \
run the reproducing test, or inspect the tree. Set purpose="test" on the run of \
the reproducing test — that is the run the comparison reads for that revision.
  - run_numerical_probe(code, label, language, expected, rtol, atol, \
residual_tol, extra_flags): run numerical code inside a revision and capture \
structured values. The recording API stores an output and one solver step per \
iteration; NaN/Inf and FP exceptions (overflow / invalid / divide-by-zero / \
underflow) are captured automatically. The probe is written in the REVISION'S \
language (Python, JavaScript, TypeScript, Java, C, C++, Rust, Go; language="auto" \
detects it) and is built inside the revision, so an import resolves to THAT \
revision's code — see the tool schema for the shape `code` must take per \
language. Use the SAME label and SAME code on both revisions so the observations \
pair up.
  - check_reported_examples(preamble, language, timeout): evaluate the \
report's OWN input/output examples in a revision. The harness builds this probe \
from the report and records every value under the example's own input text, so \
the two revisions line up automatically — you supply only the imports and \
bindings the expressions need (for SymPy: "from sympy import *" plus the symbols \
they use). Run it on BOTH revisions: it is what shows the reported buggy value \
on buggy and the expected value on fixed. Only Python/JavaScript/TypeScript \
revisions can evaluate an expression given as text; elsewhere record the \
examples with run_numerical_probe, naming each record exactly as its input.

You never plan the comparison or the verdict — those run automatically, and \
deterministically, once your plan is exhausted.

How to plan well:
  - Do the buggy revision fully before the fixed revision: installing one \
revision changes what `import` resolves to.
  - Mirror the procedure across revisions. An observation only means something \
next to its counterpart.
  - Reach for a probe when the symptom is a VALUE (NaN, Inf, wrong magnitude, \
lost precision, non-convergence) rather than a test assertion — and always when \
the strategy supplies input/output examples, since a probe built from those \
still works when the test does not exist on the buggy revision.
  - `expect` is a prediction you are accountable for, not a wish. The \
reproducing test failing on buggy is expect="test_fails"; a probe showing the \
defect on buggy is expect="pathology" and expect="no_pathology" on fixed. Use \
"success" for setup/build steps and "any" only when you genuinely cannot say.
  - You may ONLY set up the environment, build, invoke tests, and run probes. \
Never plan an edit to repository source or to the reproducing test.
  - Keep the plan tight: every step must be one you would defend."""


def build_plan_request(spec: ReproSpec) -> str:
    """The initial planning request: the mined strategy, as a hypothesis."""
    plan = spec.strategy or {}
    return "\n".join([
        f"REPOSITORY: {spec.repo}",
        f"BUG CATEGORY: {spec.bug_type or 'unknown'}",
        f"LANGUAGE: {spec.language}   (container image: {spec.image} — its toolchain is "
        f"what you build with)",
        f"BUGGY COMMIT: {spec.buggy_commit}   (at /workspace/buggy, test applied)",
        f"FIXED COMMIT: {spec.fixed_commit}   (at /workspace/fixed, test + fix present)",
        f"REPRODUCING TEST: {spec.test_selector or '(see strategy test_command)'}",
        (f"TEST CASE(S) THE PATCH ADDS — run exactly these, in "
         f"{', '.join(test_files_of(spec)) or 'the test file'}: "
         + ", ".join(test_cases_added(spec))
         + ". The selector above is what the project's CI runs and may target a "
           "different suite entirely; these names come from the patch itself."
         if test_cases_added(spec) else ""),
        *environment_block(spec),
        *test_prelude_block(spec),
        "",
        "=== MINED REPRODUCTION STRATEGY (a hypothesis, not ground truth) ===",
        f"summary: {plan.get('summary', '')}",
        f"environment_setup: {json.dumps(plan.get('environment_setup', []))}",
        f"build_steps: {json.dumps(plan.get('build_steps', []))}",
        f"reproduction_steps: {json.dumps(plan.get('reproduction_steps', []))}",
        f"test_command: {plan.get('test_command', '') or (f'pytest {spec.test_selector}' if spec.test_selector else '')}",
        f"expected_buggy_behavior: {plan.get('expected_buggy_behavior', '')}",
        f"expected_fixed_behavior: {plan.get('expected_fixed_behavior', '')}",
        f"success_criterion: {plan.get('success_criterion', 'reproducing test FAILS on buggy and PASSES on fixed')}",
        f"input_output_examples: {json.dumps(plan.get('input_output_examples', []))}",
        "  ^ check these on BOTH revisions with check_reported_examples — one call per "
        "revision, and you supply only the imports/bindings the expressions need. Their "
        "observed values are compared against the reported buggy and expected outputs "
        "above, which is how the reported behavior itself gets reproduced, not just the "
        "test outcome.",
        f"mined build_commands (fallback): {json.dumps(list(spec.build_commands))}",
        f"test dependencies (pre-installed in the container when the image has pip): "
        f"{json.dumps(list(spec.test_deps))}",
        "",
        "Produce the plan.",
    ])


def build_replan_request(spec: ReproSpec, goal: str, executed: list,
                         reflection: dict, remaining: list) -> str:
    """Compile the reflector's prose revision into the remaining executable steps."""
    return "\n".join([
        f"REPOSITORY: {spec.repo}   BUG CATEGORY: {spec.bug_type or 'unknown'}",
        f"TEST SELECTOR: {spec.test_selector}",
        *environment_block(spec),
        *test_prelude_block(spec),
        f"ORIGINAL GOAL: {goal}",
        "",
        "=== ALREADY EXECUTED (do NOT repeat what succeeded) ===",
        *(f"  {e}" for e in executed[-20:]),
        "",
        "=== ABANDONED (the rest of the previous plan) ===",
        *(f"  {s}" for s in remaining[:20]),
        "",
        "=== REFLECTION ===",
        f"failure_cause: {reflection.get('failure_cause', '')}",
        "new_steps (prose, in order — turn these into executable steps):",
        *(f"  - {s}" for s in reflection.get("new_steps", [])),
        "",
        f"MINED input_output_examples: {json.dumps((spec.strategy or {}).get('input_output_examples', []))}",
        "",
        "Produce the REVISED plan: only the steps that still need to run, as concrete "
        "tool calls. Environment work that already succeeded must not be repeated.",
    ])


# --------------------------------------------------------------------------- #
# reflector
# --------------------------------------------------------------------------- #
REFLECTOR_SYSTEM = f"""\
You are the REFLECTOR of NumReproAgent. The planner committed to a procedure for \
reproducing a numerical bug; executing it hit a failure, a contradiction, or \
finished without deciding anything. You are given the plan, what was observed, \
and the specific problem.

{_WORKSPACE}

Answer why the plan believed something false, and what should replace the rest \
of it. You do not execute anything and you do not choose tools — describe the \
new steps in prose; the planner turns them into tool calls.

Diagnose, do not patch. The interesting causes are about the plan, not the \
command:
  - regression_test_added_by_fix — the "reproducing" test ships WITH the fix, so \
on the buggy revision it does not exist or does not import. The way forward is a \
direct reproducer built from the strategy's input/output examples, run as a probe \
on both revisions, with an issue-specific oracle on the returned value.
  - test_not_sensitive_to_bug — the test runs on both revisions and passes on \
both; it does not exercise the reported defect. A probe on the specific values \
from the bug report will.
  - probe_measured_wrong_thing — the probe ran but recorded a quantity the defect \
does not affect, or compared against the wrong reference.
  - expectation_was_wrong — the observation is fine and the PLAN's expectation was \
mistaken.
  - the ordinary environment causes: missing_dependency, system_package, \
stale_selector, wrong_command, build_error.

Rules:
  - A reproducing test that FAILS on the buggy revision is the plan WORKING. If \
the problem you were shown is that, set plan_change_required=false and return no \
new steps.
  - Only the environment, the build, the test invocation, and the probes may \
change. NEVER propose editing repository source or the reproducing test — a bug \
that only reproduces after editing the code is not reproduced.
  - Prefer a repair that makes the run DECIDE something over one that merely gets \
past the error.
  - Be brief and concrete: each new step is one thing to do."""


def build_reflection_request(spec: ReproSpec, plan, problem, executed: list,
                             remaining: list, report: dict | None = None) -> str:
    """What the reflector sees: the plan, the trace, and the detected problem."""
    lines = [
        f"REPOSITORY: {spec.repo}   BUG CATEGORY: {spec.bug_type or 'unknown'}",
        f"TEST SELECTOR: {spec.test_selector}",
        *environment_block(spec),
        *test_prelude_block(spec),
        f"PLAN GOAL: {plan.goal}",
        f"PLAN ORACLE: {plan.oracle}",
        "",
        "=== PLAN ===",
        plan.outline(),
        "",
        "=== OBSERVATIONS (in execution order) ===",
        *(f"  {e}" for e in executed[-20:]),
        "",
        f"=== PROBLEM DETECTED ({problem.kind}) ===",
        f"  {problem.detail}",
    ]
    if remaining:
        lines += ["", "=== NOT YET RUN ===", *(f"  {s}" for s in remaining[:20])]
    if report:
        lines += ["", "=== DETERMINISTIC OUTCOME SO FAR ===",
                  f"  status: {report.get('status')}  ({report.get('rationale', '')})",
                  f"  transition: {report.get('transition')}   "
                  f"numeric: {report.get('numeric_verdict')}"]
    lines += [
        "",
        f"MINED input_output_examples: {json.dumps((spec.strategy or {}).get('input_output_examples', []))}",
        f"expected_buggy_behavior: {(spec.strategy or {}).get('expected_buggy_behavior', '')}",
        f"expected_fixed_behavior: {(spec.strategy or {}).get('expected_fixed_behavior', '')}",
        "",
        "Reflect: name the cause, decide whether the plan must change, and if so give "
        "the prose steps that replace the remainder.",
    ]
    return "\n".join(lines)

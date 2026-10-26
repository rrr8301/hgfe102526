from __future__ import annotations

import json

from ..planning import step_schema
from ..repro_specs import (ReproSpec, test_cases_added, test_files_of,
                           environment_block, test_prelude_block)
from .plan import CHECKS

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "description": "one sentence: what this plan establishes"},
        "milestones": {
            "type": "array",
            "description": "ordered milestones. Each is verified as soon as its steps finish, "
                           "so put a checkable outcome at every one instead of deferring "
                           "everything to the end.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string",
                             "description": "short snake_case name, e.g. buggy_environment_ready"},
                    "objective": {"type": "string", "description": "what this milestone establishes"},
                    "revision": {"type": "string", "enum": ["buggy", "fixed"],
                                 "description": "the revision its postconditions are checked against"},
                    "steps": {"type": "array", "items": step_schema()},
                    "verify": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(CHECKS)},
                        "description": "postconditions that PROVE this milestone was achieved. "
                                       "Claim only what the steps can actually show: use "
                                       "test_fails/probe_shows_defect on buggy, "
                                       "test_passes/probe_clean on fixed, and revisions_differ "
                                       "on the final milestone.",
                    },
                },
                "required": ["name", "objective", "revision", "steps", "verify"],
            },
        },
    },
    "required": ["goal", "milestones"],
}

_WORKSPACE = """\
The workspace is ALREADY prepared inside a Docker container — nothing clones, \
checks out, or explores the repository:
  - /workspace/buggy — the buggy commit, with the reproducing test applied \
(the test is present and is expected to FAIL here);
  - /workspace/fixed — the fixed commit as-is (already contains the test + the \
fix; the same test is expected to PASS here).
Installed packages persist between steps, and a step belongs to the revision its \
milestone names."""

_TOOLS = """\
Three tools are plannable:
  - run_shell_command(command, purpose, timeout): install dependencies, build, \
run the reproducing test, or inspect the tree. Set purpose="test" on the run of \
the reproducing test — that is the run verification reads for that revision.
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
deterministically, once the last milestone is verified."""

PLANNER_SYSTEM = f"""\
You are the PLANNER of NumReproAgent, which REPRODUCES a reported numerical bug \
by showing a behavior difference between two prepared revisions.

{_WORKSPACE}

{_TOOLS}

You plan in MILESTONES. A milestone is a few steps plus the postconditions that \
prove it achieved something, and it is verified the moment its steps finish — \
so a plan that cannot work is caught at the milestone where it breaks, not after \
everything has run.

How to plan well:
  - A typical shape is: buggy environment ready → the bug is OBSERVABLE on buggy \
→ fixed environment ready → the fixed revision behaves differently \
(revisions_differ). Merge or split these as the bug requires.
  - Finish the buggy revision before the fixed one: installing one revision \
changes what `import` resolves to.
  - `verify` is a claim you are held to. Claim only what the milestone's own \
steps can show — a milestone whose steps merely install packages verifies \
steps_succeeded, not test_fails. Over-claiming triggers a replan; under-claiming \
wastes the run.
  - Put revisions_differ on the final milestone. It is the only postcondition \
that asks whether the bug actually reproduced.
  - Reach for a probe when the symptom is a VALUE (NaN, Inf, wrong magnitude, \
lost precision, non-convergence) rather than a test assertion — and always when \
the strategy supplies input/output examples, since a probe built from those \
still works when the test does not exist on the buggy revision.
  - You may ONLY set up the environment, build, invoke tests, and run probes. \
Never plan an edit to repository source or to the reproducing test."""

REPLANNER_SYSTEM = f"""\
You are the REPLANNER of NumReproAgent. A milestone's postconditions were NOT \
met. You are given the plan, the observations, and exactly which checks failed \
and why. Produce the plan that replaces the failed milestone and everything \
after it.

{_WORKSPACE}

{_TOOLS}

Replan, do not retry. Ask why the milestone could not be achieved as written:
  - regression_test_added_by_fix — the "reproducing" test ships WITH the fix, so \
on the buggy revision it does not exist or does not import. Replace the \
test-based milestones with a direct reproducer built from the strategy's \
input/output examples, run as the same probe on both revisions.
  - test_not_sensitive_to_bug — the test runs and passes on both revisions; it \
does not exercise the reported defect. A probe on the specific values from the \
bug report will.
  - probe_measured_wrong_thing — the probe ran but recorded a quantity the \
defect does not affect, or compared against the wrong reference.
  - over_claimed_postcondition — the steps were fine and the milestone claimed \
something they could not show. Re-cut the milestones so each claim matches its \
steps.
  - the ordinary environment causes: a missing dependency, a system package, a \
stale selector, a wrong command, a build error.

Rules:
  - Work already done still holds: installed packages and built revisions \
persist. Do not replan steps that already succeeded.
  - Only the environment, the build, the test invocation, and the probes may \
change. NEVER propose editing repository source or the reproducing test — a bug \
that only reproduces after editing the code is not reproduced.
  - Prefer a plan that DECIDES the question over one that merely gets past the \
error. If the test can never work here, stop trying to make it work and measure \
the values instead."""


def _strategy_block(spec: ReproSpec) -> list:
    plan = spec.strategy or {}
    return [
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
    ]


def build_plan_request(spec: ReproSpec) -> str:
    """The initial planning request."""
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
        *_strategy_block(spec),
        "",
        "Produce the milestone plan.",
    ])


def build_replan_request(spec: ReproSpec, goal: str, milestone, verification,
                         executed: list, remaining: list) -> str:
    """What the replanner sees: the failed milestone, its report, and the trace."""
    failed = [r for r in verification.results if not r["passed"]]
    return "\n".join([
        f"REPOSITORY: {spec.repo}   BUG CATEGORY: {spec.bug_type or 'unknown'}",
        f"TEST SELECTOR: {spec.test_selector}",
        f"ORIGINAL GOAL: {goal}",
        *environment_block(spec),
        *test_prelude_block(spec),
        "",
        f"=== FAILED MILESTONE: {milestone.name} ===",
        f"  objective: {milestone.objective}   (revision: {milestone.revision})",
        "  failed checks:",
        *(f"    - {r['check']}: {r['detail']}" for r in failed),
        *(f"    + {r['check']}: {r['detail']} (passed)"
          for r in verification.results if r["passed"]),
        "",
        "=== OBSERVATIONS SO FAR (work that already succeeded still holds) ===",
        *(f"  {e}" for e in executed[-25:]),
        "",
        "=== MILESTONES NOT YET REACHED (you are replacing these) ===",
        *(f"  {m}" for m in remaining[:10]),
        "",
        *_strategy_block(spec),
        "",
        "Produce the REVISED plan: the milestones that still need to run, starting from "
        "whatever now replaces the failed one.",
    ])

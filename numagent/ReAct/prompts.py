from __future__ import annotations

import json

from ..repro_specs import (ReproSpec, test_cases_added, test_files_of,
                           environment_block, test_prelude_block)

_SYSTEM = """\
You are NumReproAgent, an autonomous engineer that REPRODUCES a reported numerical \
bug by running its reproduction procedure on two already-prepared revisions and \
confirming the buggy→fixed behavior transition.

The workspace is ALREADY prepared inside a Docker container — you do NOT clone, \
checkout, or explore the repository:
  - /workspace/buggy — the buggy commit, with the reproducing test applied \
(the test is present and is expected to FAIL here);
  - /workspace/fixed — the fixed commit as-is (already contains the test + the \
fix; the same test is expected to PASS here).

You are given a mined STRATEGY (bug category, environment setup, build steps, \
reproduction steps, test command, expected buggy/fixed behavior, success \
criterion, and optional input/output examples). Treat the strategy as a \
HYPOTHESIS, not ground truth — repair it yourself when a command fails.

Your {n_tools} tools:
  - run_shell_command(command, working_dir, purpose, timeout): run any setup, \
build, test, inspection, or debugging command against a revision. Label the run \
of the reproducing test with purpose="test" — that is the run the comparison \
reads. You get stdout, stderr, exit code, timeout flag, and runtime back.
  - run_numerical_probe(code, working_dir, label, language, expected, rtol, atol, \
residual_tol, extra_flags): run issue-specific numerical code inside a revision \
and capture structured values — a recording API (probe / Probe / probe_scalar, \
depending on the language) records an output, an iteration API records one solver \
step, and NaN/Inf plus FP exceptions (overflow / invalid / divide-by-zero / \
underflow) are captured automatically. The probe is written in the REVISION'S \
language — Python, JavaScript, TypeScript, Java, C, C++, Rust, or Go — and is \
built inside the revision, so an import resolves to THAT revision's code; \
language="auto" detects it. Read the tool schema for the exact shape `code` must \
take in that language (compiled languages want a body function, not a main). \
Use it when the bug's symptom is a VALUE (NaN, Inf, wrong magnitude, lost \
precision, non-convergence) rather than a test assertion — and run the SAME \
label with the SAME code on both revisions so they pair up.
  - check_reported_examples(working_dir, preamble, language): evaluate the \
report's OWN input/output examples in a revision. The harness builds the probe \
from the report and records each value under the example's own input text, so the \
revisions line up automatically — you supply only the imports and bindings the \
expressions need ("from sympy import *" plus the symbols they use, a require() of \
the module under test, …). Run it on BOTH revisions: it is what shows the reported \
buggy value on buggy and the expected value on fixed. Python/JavaScript/TypeScript \
only; in another language record the examples with run_numerical_probe, naming \
each record exactly as its input.
  - compare_execution_results(): deterministically compares the buggy and fixed \
observations. Call it AFTER both revisions have been exercised.{packaging_tool}
  - finalize_reproduction(notes): produces the final status + evidence report. \
Call it LAST, exactly once; it ends the task.

PROCEDURE (follow in order):
  1. In /workspace/buggy: run the environment-setup and build commands \
(purpose="setup"/"build"), then run the reproducing test with purpose="test". \
Run check_reported_examples when the strategy lists input/output examples, and \
add a numerical probe if the expected behavior is about values it does not cover.
  2. Repeat exactly the same procedure — and the same probe labels — in \
/workspace/fixed.
  3. compare_execution_results(){packaging_step} then finalize_reproduction(), \
and stop.

Rules:
  - A test ASSERTION failure on the buggy revision is SUCCESS, not something to \
repair. Only build/collection/import errors (the test cannot run) are repairable.
  - When a command fails, diagnose it from its stderr and fix it yourself: \
install the missing dependency or system package, correct the flag, or recover \
a stale test selector. Retry a failing step at most 3 times, then move on.
  - You may ONLY repair the environment / build / test invocation. Never edit \
repository source or the reproducing test.
  - Do the buggy revision fully before the fixed revision (installing one \
revision changes what `import` resolves to).
  - Be efficient. Before each tool call, briefly state your Thought: what you \
just observed, your updated hypothesis, and why this action. Do not repeat a \
command that already succeeded. Reach finalize_reproduction promptly.
"""


_PACKAGING_TOOL = """
  - package_benchmark_image(notes): snapshot this reproduction as the \
self-contained benchmark image <repo>-<issue_id> — both revisions exactly as \
they stand (built, with everything you installed), the source and test patches, \
and every probe you ran, replayable on either revision. Call it ONCE, after \
compare_execution_results confirms the defect on buggy and its absence on fixed, \
and BEFORE finalize_reproduction: nothing you do after that call is captured. \
Skip it when the comparison did not confirm the bug — a snapshot of a \
reproduction that did not happen is worth nothing.\
"""

#: what step 3 of the procedure becomes when packaging is available
_PACKAGING_STEP = (", then package_benchmark_image() if it confirms the "
                   "buggy\u2192fixed transition,")


def system(packaging: bool = False) -> str:
    """The ReAct system prompt. `packaging` offers the benchmark-image tool —
    it is only mentioned when the run can actually use it."""
    return _SYSTEM.format(
        n_tools="six" if packaging else "five",
        packaging_tool=_PACKAGING_TOOL if packaging else "",
        packaging_step=_PACKAGING_STEP if packaging else "")


#: the prompt without packaging, for callers that just want the text
SYSTEM = system()


def build_task(spec: ReproSpec) -> str:
    """The per-issue task briefing (the strategy the agent executes)."""
    plan = spec.strategy or {}
    return "\n".join([
        f"REPOSITORY: {spec.repo}",
        f"BUG CATEGORY: {spec.bug_type or 'unknown'}",
        f"LANGUAGE: {spec.language}   (container image: {spec.image} — its toolchain is "
        f"what you build with)",
        f"BUGGY COMMIT: {spec.buggy_commit}   (checked out at /workspace/buggy, test applied)",
        f"FIXED COMMIT: {spec.fixed_commit}   (checked out at /workspace/fixed, test + fix present)",
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
        "=== MINED REPRODUCTION STRATEGY (a hypothesis) ===",
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
        "Reproduce the bug now: build both revisions, run the reproducing test (and a "
        "numerical probe if the symptom is a value) on each, repair failures, then "
        "compare_execution_results and finalize_reproduction.",
    ])

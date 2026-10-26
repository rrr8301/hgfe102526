"""Self-reflective planning: plan once, execute, reflect only when reality objects.

    strategy → PLANNER → plan → tools → observations
                                          ↓
                       failure / contradiction / inconclusive?
                        ├── no  → continue the plan
                        └── yes → REFLECTOR → revised plan → tools …

Two properties are deliberate:

* **Reflection is not a tool.** The toolset is exactly the shared four. The
  reflector is an LLM call the orchestrator makes, on its own terms, about a
  problem the orchestrator detected — so the agent cannot decide to stop
  reflecting, nor reflect its way around an inconvenient observation.
* **Detection is deterministic, diagnosis is not.** A step carries the
  expectation that justified it; disagreement is found by code
  (:func:`~numagent.Reflective.plan.assess`), and only the *why* is asked of a
  model.

Contrast with ReAct, which re-decides after every observation: here the model
reasons a few times about the whole procedure instead of many times about the
next command.
"""

from __future__ import annotations

import json

from ..agent.llm.base import LLMClient, build_client, complete_json, complete_json_turn
from ..planning import (MAX_PLAN_RETRIES, decide_packaging,
                        ensure_described_examples, ensure_examples_observed,
                        ensure_reproducing_test_ran,
                        ensure_test_on_both_revisions,
                        execute_step, summarize_step, unusable_plan_reason)
from ..repro_package import PackageOptions
from ..repro_session import AgentReproResult, run_session
from ..repro_specs import ReproSpec
from ..repro_tools import RunContext, compare_execution_results, finalize_reproduction
from . import prompts
from .plan import INCONCLUSIVE, Plan, Problem, assess, plan_from_raw

NAME = "reflective"

#: Statuses that answer the question. Anything else is worth another look.
DECISIVE = ("REPRODUCED", "PARTIALLY_REPRODUCED")


# --------------------------------------------------------------------------- #
# the two LLM roles
# --------------------------------------------------------------------------- #
def _plan(ctx: RunContext, client: LLMClient, user: str) -> tuple:
    """One planning call. Returns the plan and, when it has no executable step,
    WHY — the note the next attempt is given."""
    ctx.iterations += 1
    raw, turn = complete_json_turn(client, prompts.PLANNER_SYSTEM, user,
                                   prompts.PLAN_SCHEMA, "submit_plan",
                                   "Submit the reproduction plan.")
    plan = plan_from_raw(raw)
    ctx.history.append({"planner": {"goal": plan.goal, "oracle": plan.oracle,
                                    "n_steps": len(plan.steps),
                                    "steps": [s.describe() for s in plan.steps]}})
    ctx.emit("thought", f"PLAN ({len(plan.steps)} steps): {plan.goal}\n{plan.outline()}")
    return plan, ("" if plan.steps else unusable_plan_reason(raw, turn.stop_reason))


def _plan_until_executable(ctx: RunContext, client: LLMClient, user: str,
                           retries: int) -> tuple:
    """Plan, and re-ask with the reason when the answer had nothing to execute.

    A plan that cannot be run is a failure of the planning call, not of the
    instance: nothing has been executed, so abandoning the run here reports a
    truncated or malformed answer as though the repository were unbuildable.
    Each retry carries the diagnosis, so the next attempt differs from the last.
    """
    plan, reason = _plan(ctx, client, user)
    for attempt in range(retries):
        if plan.steps:
            break
        ctx.history.append({"plan_retry": {"attempt": attempt + 1, "reason": reason}})
        ctx.emit("thought", f"PLAN UNUSABLE ({attempt + 1}/{retries}): {reason}")
        plan, reason = _plan(ctx, client, f"{user}\n\n{reason}")
    return plan, reason


def _reflect(ctx: RunContext, client: LLMClient, user: str) -> dict:
    ctx.iterations += 1
    raw = complete_json(client, prompts.REFLECTOR_SYSTEM, user, prompts.REFLECTION_SCHEMA,
                        "submit_reflection", "Submit the reflection.")
    reflection = {
        "failure_cause": str(raw.get("failure_cause") or "unknown"),
        # an empty/garbled reflection must not silently become "carry on"
        "plan_change_required": bool(raw.get("plan_change_required", bool(raw.get("new_steps")))),
        "new_steps": [str(s) for s in (raw.get("new_steps") or []) if str(s).strip()],
    }
    ctx.history.append({"reflector": reflection})
    ctx.emit("thought", f"REFLECT: {reflection['failure_cause']} — "
                        f"plan_change_required={reflection['plan_change_required']}"
                        + ("\n  - " + "\n  - ".join(reflection["new_steps"])
                           if reflection["new_steps"] else ""))
    return reflection


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def _summarize(step, record: dict | None, problem: Problem | None) -> str:
    """One line per executed step — the trace both LLM roles reason over."""
    note = f" !! {problem.kind}: {problem.detail}" if problem else " ok"
    return summarize_step(step, record, note)


# --------------------------------------------------------------------------- #
# the orchestrator
# --------------------------------------------------------------------------- #
def _drive(ctx: RunContext, client: LLMClient, max_reflections: int,
           max_steps: int) -> str:
    # the tools may GENERATE code (numagent.probe_codegen), which needs the
    # run's own client rather than a second one built from the environment
    ctx.llm = client
    spec = ctx.spec
    plan, reason = _plan_until_executable(
        ctx, client, prompts.build_plan_request(spec),
        min(max_reflections, MAX_PLAN_RETRIES))
    if not plan.steps:
        return f"planner produced no executable steps — {reason}"

    executed: list[str] = []          # the running trace, in prose
    reflections = 0
    steps_run = 0

    while True:
        # --- execute the plan, watching each step against its expectation ----
        interrupted = False
        for i, step in enumerate(plan.steps):
            if steps_run >= max_steps:
                return f"stopped after {max_steps} executed steps"
            steps_run += 1
            record = execute_step(ctx, step)
            problem = assess(step, record, i)
            executed.append(_summarize(step, record, problem))
            if problem is None:
                continue
            # the expectation that broke is this strategy's distinctive evidence
            ctx.history.append({"expectation_broken": problem.as_dict(),
                                "step": step.describe(), "expected": step.expect})

            # --- failure / contradiction: reflect, or accept and continue ----
            if reflections >= max_reflections:
                continue                       # out of reflections: run it out
            remaining = [s.describe() for s in plan.steps[i + 1:]]
            reflection = _reflect(ctx, client, prompts.build_reflection_request(
                spec, plan, problem, executed, remaining))
            reflections += 1
            if not reflection["plan_change_required"]:
                continue                       # the plan was right; carry on
            plan, reason = _plan(ctx, client, prompts.build_replan_request(
                spec, plan.goal, executed, reflection, remaining))
            if not plan.steps:
                return f"reflection {reflections} produced no executable steps — {reason}"
            interrupted = True
            break                              # restart on the revised plan

        if interrupted:
            continue

        # --- plan exhausted: grade it deterministically -----------------------
        ensure_reproducing_test_ran(ctx)        # the test the patch adds
        ensure_test_on_both_revisions(ctx)      # the revision the plan skipped
        if ctx.probes:
            ensure_examples_observed(ctx, client)   # the report's own examples
            ensure_described_examples(ctx, client)  # …and the prose ones
        compare_execution_results(ctx)
        report = finalize_reproduction(ctx)
        if report["status"] in DECISIVE or reflections >= max_reflections:
            decide_packaging(ctx, client)   # the outcome is accepted: keep it?
            return ""

        # --- inconclusive: the third reason to reflect ------------------------
        problem = Problem(INCONCLUSIVE,
                          f"the plan ran to the end and the deterministic outcome is "
                          f"{report['status']}: {report['rationale']}")
        ctx.history.append({"expectation_broken": problem.as_dict()})
        reflection = _reflect(ctx, client, prompts.build_reflection_request(
            spec, plan, problem, executed, [], report))
        reflections += 1
        if not reflection["plan_change_required"]:
            decide_packaging(ctx, client)      # the reflector accepts the outcome
            return ""
        plan, reason = _plan(ctx, client, prompts.build_replan_request(
            spec, plan.goal, executed, reflection, []))
        if not plan.steps:
            ctx.history.append({"plan_retry": {"attempt": "replan", "reason": reason}})
            return ""
        # a fresh attempt must be graded fresh, not against the abandoned one
        ctx.comparison, ctx.report = None, None


def reproduce(spec: ReproSpec, *, client: LLMClient | None = None,
              provider: str = "anthropic", model: str = "", work_dir,
              token: str | None = None, max_iterations: int = 30,
              max_reflections: int = 3, cmd_timeout_s: float = 1800.0,
              keep_container: bool = False, package: PackageOptions | None = None,
              stream_output: bool = False, probes: bool = True,
              on_event=None) -> AgentReproResult:
    """Reproduce one bug by planning, executing, and reflecting when reality objects.

    `max_iterations` bounds executed tool steps (tool execution costs no reasoning
    here); `max_reflections` bounds how many times the plan may be revised.
    ``result.iterations`` counts LLM calls — plan, reflect, replan — so it stays
    comparable with the per-action turns a ReAct run reports.
    """
    client = client or build_client(provider, model)
    return run_session(
        spec, reasoning=NAME, work_dir=work_dir, token=token,
        cmd_timeout_s=cmd_timeout_s, keep_container=keep_container,
        package=package, stream_output=stream_output, probes=probes,
        on_event=on_event,
        driver=lambda ctx: _drive(ctx, client, max_reflections, max_iterations))

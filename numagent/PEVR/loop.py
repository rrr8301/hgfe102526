from __future__ import annotations

from ..agent.llm.base import LLMClient, build_client, complete_json_turn
from .. import repro_compare
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
from .plan import MilestonePlan, plan_from_raw, verify_execution

NAME = "pevr"

#: How many times one objective may fail the SAME checks on the same revision
#: before it is abandoned rather than replanned again. A milestone that cannot be
#: achieved as written must not consume the whole replan budget: the milestones
#: behind it — including everything on the other revision — never get to run, and
#: an instance whose test already failed on buggy is graded as if the fixed
#: revision had refused to build, when in truth it was never touched.
ABANDON_AFTER = 2


# --------------------------------------------------------------------------- #
# the LLM roles — one schema, two jobs
# --------------------------------------------------------------------------- #
def _plan(ctx: RunContext, client: LLMClient, system: str, user: str,
          kind: str) -> tuple:
    """One planning call. Returns the plan and, when it has no executable
    milestone, WHY — the note the next attempt is given."""
    ctx.iterations += 1
    raw, turn = complete_json_turn(client, system, user, prompts.PLAN_SCHEMA,
                                   "submit_plan", "Submit the milestone plan.")
    plan = plan_from_raw(raw)
    ctx.history.append({kind: {"goal": plan.goal,
                               "milestones": [m.describe() for m in plan.milestones]}})
    ctx.emit("thought", f"{kind.upper()} ({len(plan.milestones)} milestones): "
                        f"{plan.goal}\n{plan.outline()}")
    reason = "" if plan.milestones else unusable_plan_reason(raw, turn.stop_reason)
    return plan, reason


def _plan_until_executable(ctx: RunContext, client: LLMClient, system: str,
                           user: str, kind: str, retries: int) -> tuple:
    """Plan, and re-ask with the reason when the answer had nothing to execute.

    A plan that cannot be run is a *reportable failure of the planning call*, not
    of the instance: the run has spent nothing and learned nothing, and abandoning
    it here is what turns a truncated answer into a SETUP_FAILURE that looks like
    a broken repository. Each retry carries the diagnosis, so the model is not
    asked the identical question that already failed.
    """
    plan, reason = _plan(ctx, client, system, user, kind)
    for attempt in range(retries):
        if plan.milestones:
            break
        ctx.history.append({"plan_retry": {"attempt": attempt + 1, "reason": reason}})
        ctx.emit("thought", f"PLAN UNUSABLE ({attempt + 1}/{retries}): {reason}")
        plan, reason = _plan(ctx, client, system, f"{user}\n\n{reason}", kind)
    return plan, reason


# --------------------------------------------------------------------------- #
# the executor
# --------------------------------------------------------------------------- #
def _execute_milestone(ctx: RunContext, milestone, budget: int) -> tuple:
    """Run a milestone's steps. Returns [(step, record)] and the steps consumed."""
    observations = []
    for step in milestone.steps:
        if len(observations) >= budget:
            break
        observations.append((step, execute_step(ctx, step)))
    return observations, len(observations)


# --------------------------------------------------------------------------- #
# the orchestrator
# --------------------------------------------------------------------------- #
def _transition_established(ctx) -> bool:
    """Has the reproducing test already been seen to FAIL on buggy and PASS on
    fixed? Under `--no-probes` that is the whole of the evidence, so the run has
    nothing left to learn and stops there rather than spending its budget."""
    b = repro_compare.parse_test_outcome((ctx.results.get("buggy") or {}).get("test"))
    f = repro_compare.parse_test_outcome((ctx.results.get("fixed") or {}).get("test"))
    return b["status"] == "fail" and f["status"] == "pass"


def _drive(ctx: RunContext, client: LLMClient, max_replans: int, max_steps: int) -> str:
    # the tools may GENERATE code (numagent.probe_codegen), which needs the
    # run's own client rather than a second one built from the environment
    ctx.llm = client
    spec = ctx.spec
    plan, reason = _plan_until_executable(
        ctx, client, prompts.PLANNER_SYSTEM, prompts.build_plan_request(spec),
        "planner", min(max_replans, MAX_PLAN_RETRIES))
    if not plan.milestones:
        return f"planner produced no executable milestones — {reason}"

    executed: list[str] = []          # the running trace, in prose
    stuck: dict = {}                  # (revision, failed checks) -> times seen
    replans = 0
    steps_run = 0
    index = 0

    while index < len(plan.milestones):
        milestone = plan.milestones[index]
        if steps_run >= max_steps:
            return f"stopped after {max_steps} executed steps"
        if not ctx.probes and _transition_established(ctx):
            ctx.emit("thought", "FAIL->PASS established and probes are disabled "
                                "(--no-probes) — stopping here")
            break

        # --- execute -----------------------------------------------------
        observations, used = _execute_milestone(ctx, milestone, max_steps - steps_run)
        steps_run += used
        executed += [summarize_step(s, r, f"   [{milestone.name}]")
                     for s, r in observations]

        # --- verify ------------------------------------------------------
        verification = verify_execution(ctx, milestone, observations)
        ctx.history.append({"verification": verification.as_dict()})
        ctx.emit("tool", {"name": "verify_execution", "milestone": milestone.name,
                          "status": verification.summary()})
        if verification.verified:
            index += 1
            continue

        # --- abandon a milestone that keeps failing the same way ----------
        # Milestone NAMES change with every replan, so the identity that matters
        # is what actually went wrong: the revision plus the checks that failed.
        signature = (milestone.revision, tuple(sorted(verification.failed)))
        stuck[signature] = stuck.get(signature, 0) + 1
        if stuck[signature] >= ABANDON_AFTER:
            note = (f"{milestone.name} [{milestone.revision}] ABANDONED after "
                    f"{stuck[signature]} attempts failing "
                    f"{', '.join(verification.failed)} — continuing with the rest")
            executed.append(note)
            ctx.history.append({"abandoned_milestone": {
                "milestone": milestone.name, "revision": milestone.revision,
                "failed": verification.failed, "attempts": stuck[signature],
                "detail": [r["detail"] for r in verification.results if not r["passed"]][:2]}})
            ctx.emit("thought", f"ABANDON {note}")
            index += 1                 # the other revision still deserves its turn
            continue

        # --- replan ------------------------------------------------------
        if replans >= max_replans:
            break                      # out of budget: grade what we have
        remaining = [m.describe() for m in plan.milestones[index + 1:]]
        revised, revised_reason = _plan(
            ctx, client, prompts.REPLANNER_SYSTEM,
            prompts.build_replan_request(spec, plan.goal, milestone, verification,
                                         executed, remaining),
            "replanner")
        replans += 1
        if not revised.milestones:
            ctx.history.append({"plan_retry": {"attempt": "replan", "reason": revised_reason}})
            break
        plan, index = revised, 0       # the revised plan replaces the remainder

    # the second revision, if the plan never got to it, then the report's own
    # examples — both deterministic, both only when something is missing
    ensure_reproducing_test_ran(ctx)
    ensure_test_on_both_revisions(ctx)
    if ctx.probes:
        # --no-probes: the test transition is the whole oracle, so the report's
        # values are never measured and no probe is generated for them
        ensure_examples_observed(ctx, client)
        ensure_described_examples(ctx, client)
    compare_execution_results(ctx)
    decide_packaging(ctx, client)      # no LLM call unless packaging is enabled
    finalize_reproduction(ctx)
    return ""


def reproduce(spec: ReproSpec, *, client: LLMClient | None = None,
              provider: str = "anthropic", model: str = "", work_dir,
              token: str | None = None, max_iterations: int = 30,
              max_replans: int = 3, cmd_timeout_s: float = 1800.0,
              keep_container: bool = False, package: PackageOptions | None = None,
              stream_output: bool = False, probes: bool = True,
              on_event=None) -> AgentReproResult:
    """Reproduce one bug by planning milestones, verifying each, and replanning.

    `max_iterations` bounds executed tool steps (execution costs no reasoning
    here); `max_replans` bounds how many times a failed verification may rewrite
    the remainder of the plan. ``result.iterations`` counts LLM calls — the
    initial plan plus one per replan — so it stays comparable with the per-action
    turns a ReAct run reports.
    """
    client = client or build_client(provider, model)
    return run_session(
        spec, reasoning=NAME, work_dir=work_dir, token=token,
        cmd_timeout_s=cmd_timeout_s, keep_container=keep_container,
        package=package, stream_output=stream_output, probes=probes,
        on_event=on_event,
        driver=lambda ctx: _drive(ctx, client, max_replans, max_iterations))

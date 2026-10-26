"""The plan this strategy executes, and the check that decides when to reflect.

Each :class:`~numagent.planning.Step` carries the *expectation* that justified
it, so executing it yields more than an observation: it yields agreement or
disagreement with what the planner believed. :func:`assess` turns that into the
trigger the orchestrator reflects on.

Detecting the problem stays deterministic; only *diagnosing* it is an LLM call.
That split is what keeps a reflection honest — the agent cannot talk itself out
of a contradiction it did not want to see, nor invent one it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..planning import Step, steps_from_raw
from ..repro_compare import parse_test_outcome

# problem kinds, in the order the orchestrator's diagram names them
FAILURE = "failure"                # the step could not run
CONTRADICTION = "contradiction"    # it ran, and disagreed with the expectation
INCONCLUSIVE = "inconclusive"      # the whole plan ran and decided nothing


@dataclass
class Plan:
    goal: str = ""
    oracle: str = ""
    steps: list = field(default_factory=list)

    def outline(self) -> str:
        return "\n".join(f"  {i + 1}. {s.describe()}  (expect: {s.expect})"
                         for i, s in enumerate(self.steps)) or "  (no steps)"


@dataclass
class Problem:
    kind: str
    detail: str
    step_index: int | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "step_index": self.step_index}


def plan_from_raw(raw: dict) -> Plan:
    """Build a Plan from the planner's JSON (unrunnable steps are dropped)."""
    return Plan(goal=str(raw.get("goal") or "")[:500],
                oracle=str(raw.get("oracle") or "")[:500],
                steps=steps_from_raw(raw.get("steps")))


def assess(step: Step, record: dict | None, index: int | None = None) -> Problem | None:
    """Compare one step's observation against its expectation.

    `record` is the stored shell record or the probe observation. Returns None
    when the step agreed with the plan — including the case that matters most:
    a reproducing test that FAILS on the buggy revision is the expectation being
    met, not a problem to fix.
    """
    if record is None:
        return Problem(FAILURE, "the step produced no observation", index)
    if step.expect == "any":
        return None
    if step.tool == "run_numerical_probe":
        return _assess_probe(step, record, index)
    return _assess_shell(step, record, index)


def _assess_shell(step: Step, record: dict, index: int | None) -> Problem | None:
    code = record.get("exit_code")
    if step.expect in ("test_fails", "test_passes"):
        status = parse_test_outcome(record)["status"]
        if status in ("error", "timeout", "no_tests", "missing"):
            return Problem(FAILURE, f"the test could not run (status={status}): "
                                    f"{_tail(record)}", index)
        want = "fail" if step.expect == "test_fails" else "pass"
        if status != want:
            return Problem(CONTRADICTION,
                           f"planned for the test to {want} here, but it "
                           f"{status}ed on {step.revision}", index)
        return None
    if step.expect == "success" and (record.get("timed_out") or code != 0):
        return Problem(FAILURE, f"`{record.get('command', '')[:120]}` exited {code}: "
                                f"{_tail(record)}", index)
    return None


def _assess_probe(step: Step, obs: dict, index: int | None) -> Problem | None:
    present = (obs.get("pathology") or {}).get("present")
    reasons = (obs.get("pathology") or {}).get("reasons") or []
    if present is None:                       # the probe itself never ran
        return Problem(FAILURE, f"the probe did not complete: "
                                f"{reasons[0] if reasons else obs.get('status', '')}", index)
    if step.expect == "pathology" and not present:
        return Problem(CONTRADICTION,
                       f"planned for the numerical defect to show on {step.revision}, "
                       f"but the probe found clean values: {obs.get('values', [])[:6]}", index)
    if step.expect == "no_pathology" and present:
        return Problem(CONTRADICTION,
                       f"planned for clean values on {step.revision}, but the probe "
                       f"found: {'; '.join(reasons[:2])}", index)
    return None


def _tail(record: dict) -> str:
    text = (record.get("stderr") or record.get("stdout") or "").strip()
    return text[-300:] if text else "(no output)"

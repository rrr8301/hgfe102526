from __future__ import annotations

from dataclasses import dataclass, field

from ..planning import steps_from_raw
from ..repro_compare import FLIPPED, compare, compare_examples, parse_test_outcome

#: Postconditions a milestone may claim. Each is checkable from the observations
#: alone — a milestone cannot be verified by assertion.
CHECKS = (
    "steps_succeeded",      # every step in this milestone ran cleanly
    "test_ran",             # this revision produced a definite test outcome
    "test_fails",           # …and it was a failure (the bug, on buggy)
    "test_passes",          # …or a pass (the fix, on fixed)
    "probe_ran",            # this milestone's probes completed
    "probe_shows_defect",   # …and at least one exhibits the numerical defect
    "probe_clean",          # …or none of them do
    "revisions_differ",     # buggy and fixed now demonstrably disagree
    "examples_reproduced",  # the mined input/output examples show buggy != expected == fixed
)


@dataclass
class Milestone:
    name: str
    objective: str
    revision: str
    steps: list = field(default_factory=list)
    verify: list = field(default_factory=list)

    def describe(self) -> str:
        return (f"{self.name} [{self.revision}] — {self.objective} "
                f"({len(self.steps)} steps, verify: {', '.join(self.verify) or 'none'})")


@dataclass
class MilestonePlan:
    goal: str = ""
    milestones: list = field(default_factory=list)

    def outline(self) -> str:
        return "\n".join(f"  {i + 1}. {m.describe()}"
                         for i, m in enumerate(self.milestones)) or "  (no milestones)"


@dataclass
class Verification:
    milestone: str
    verified: bool
    results: list = field(default_factory=list)

    @property
    def failed(self) -> list:
        return [r["check"] for r in self.results if not r["passed"]]

    def summary(self) -> str:
        if self.verified:
            return f"{self.milestone}: verified ({', '.join(r['check'] for r in self.results) or 'no checks'})"
        return f"{self.milestone}: FAILED {', '.join(self.failed)}"

    def as_dict(self) -> dict:
        return {"milestone": self.milestone, "verified": self.verified,
                "results": self.results}


def plan_from_raw(raw: dict) -> MilestonePlan:
    """Build a milestone plan from the planner's JSON, dropping empty milestones."""
    milestones = []
    for entry in raw.get("milestones") or []:
        if not isinstance(entry, dict):
            continue
        revision = "fixed" if entry.get("revision") == "fixed" else "buggy"
        steps = steps_from_raw(entry.get("steps"), default_revision=revision)
        if not steps:
            continue
        checks = [c for c in (entry.get("verify") or []) if c in CHECKS]
        milestones.append(Milestone(
            name=str(entry.get("name") or f"milestone_{len(milestones) + 1}")[:80],
            objective=str(entry.get("objective") or "")[:300],
            revision=revision, steps=steps,
            # a milestone claiming nothing checkable at least has to have run
            verify=checks or ["steps_succeeded"]))
    return MilestonePlan(goal=str(raw.get("goal") or "")[:500], milestones=milestones)


def verify_execution(ctx, milestone: Milestone, observations: list) -> Verification:
    """Check a milestone's postconditions against what its steps produced.

    `observations` is the [(step, record)] this milestone just produced; the
    revision-wide checks read the context, so a milestone can also confirm
    something an earlier one established.
    """
    results = []
    for check in milestone.verify:
        passed, detail = _CHECKS[check](ctx, milestone, observations)
        results.append({"check": check, "passed": bool(passed), "detail": detail})
    return Verification(milestone=milestone.name,
                        verified=all(r["passed"] for r in results),
                        results=results)


def _steps_succeeded(ctx, milestone, observations):
    bad = []
    for step, record in observations:
        if record is None:
            bad.append(f"{step.describe()} did not execute")
        elif step.tool == "run_numerical_probe":
            if (record.get("pathology") or {}).get("present") is None:
                bad.append(f"probe {step.arguments.get('label')!r} did not complete: "
                           f"{record.get('status')}")
        elif record.get("timed_out") or record.get("exit_code") != 0:
            bad.append(f"`{str(record.get('command', ''))[:80]}` exited "
                       f"{record.get('exit_code')}: {_tail(record)}")
    return not bad, "; ".join(bad[:3]) if bad else "every step completed"


def _test_status(ctx, revision: str) -> str:
    return parse_test_outcome((ctx.results.get(revision) or {}).get("test"))["status"]


def _test_detail(ctx, revision: str, status: str, want: str = "") -> str:
    head = f"test {status} on {revision}"
    if want:
        head += f" (milestone required {want})"
    if status in ("pass", "fail"):
        return head
    record = (ctx.results.get(revision) or {}).get("test")
    if record is None:
        return f"{head}: no test run was recorded on this revision"
    return f"{head}: {_tail(record, 400)}"


def _test_ran(ctx, milestone, observations):
    status = _test_status(ctx, milestone.revision)
    return status in ("pass", "fail"), _test_detail(ctx, milestone.revision, status)


def _test_is(want: str):
    def check(ctx, milestone, observations):
        status = _test_status(ctx, milestone.revision)
        return status == want, _test_detail(ctx, milestone.revision, status, want)
    return check


def _probes(ctx, milestone, observations) -> list:
    """This milestone's probe observations, by the labels its steps used."""
    labels = [s.arguments.get("label") for s, _ in observations
              if s.tool == "run_numerical_probe"]
    store = (ctx.results.get(milestone.revision) or {}).get("probes") or {}
    return [store[l] for l in labels if l in store]


def _probe_failure(obs: dict) -> str:
    """Why one probe never produced an observation — named, not just counted.

    A replanner told "probe_error" rewrites the probe and hits the same wall; the
    same failure quoting `ImportError: cannot import name 'manualintegrate' from
    'sympy'` is one edit away from working. The reason is already in the
    observation, so withholding it is what makes the retry blind.
    """
    reasons = (obs.get("pathology") or {}).get("reasons") or []
    why = reasons[0] if reasons else (obs.get("status") or "no structured output")
    tb = [ln for ln in (obs.get("traceback_tail") or "").strip().splitlines() if ln.strip()]
    tail = f" | {tb[-1].strip()[:200]}" if tb else ""
    return f"{obs.get('label')!r} ({obs.get('status')}): {why}{tail}"


def _probe_ran(ctx, milestone, observations):
    obs = _probes(ctx, milestone, observations)
    if not obs:
        return False, "the milestone ran no probe"
    broken = [o for o in obs if (o.get("pathology") or {}).get("present") is None]
    if not broken:
        return True, "all probes completed"
    return False, (f"{len(broken)} of {len(obs)} probe(s) did not complete: "
                   + "; ".join(_probe_failure(o) for o in broken[:2]))


def _probe_shows_defect(ctx, milestone, observations):
    obs = _probes(ctx, milestone, observations)
    if not obs:
        return False, "the milestone ran no probe"
    hits = [o for o in obs if (o.get("pathology") or {}).get("present")]
    if hits:
        reasons = (hits[0].get("pathology") or {}).get("reasons") or []
        return True, f"probe {hits[0].get('label')!r}: {'; '.join(reasons[:2])}"
    broken = [o for o in obs if (o.get("pathology") or {}).get("present") is None]
    if broken:      # nothing was exhibited because nothing ran — say which and why
        return False, (f"no probe could be judged on {milestone.revision}: "
                       + "; ".join(_probe_failure(o) for o in broken[:2]))
    return False, (f"no probe exhibited the defect on {milestone.revision} "
                   f"(values {obs[0].get('values', [])[:4]})")


def _probe_clean(ctx, milestone, observations):
    obs = _probes(ctx, milestone, observations)
    if not obs:
        return False, "the milestone ran no probe"
    dirty = [o for o in obs if (o.get("pathology") or {}).get("present") is not False]
    if not dirty:
        return True, f"every probe is clean on {milestone.revision}"
    reasons = (dirty[0].get("pathology") or {}).get("reasons") or []
    return False, (f"probe {dirty[0].get('label')!r} is not clean on "
                   f"{milestone.revision}: {'; '.join(reasons[:2])}")


def _examples_reproduced(ctx, milestone, observations):
    """The reported values themselves: buggy differs from the expected output and
    fixed now matches it, for the examples the strategy mined."""
    result = compare_examples(ctx.results.get("buggy") or {},
                              ctx.results.get("fixed") or {}, ctx.spec)
    if result["verdict"] is None:
        return False, "the strategy mined no input/output examples to check"
    ok = result["verdict"] == "reproduced"
    detail = (f"{result['n_reproduced']}/{result['n_examples']} mined example(s) "
              f"reproduced (verdict {result['verdict']}, "
              f"{result.get('n_contested', 0)} still wrong on fixed)")
    if result.get("n_unobserved"):
        # "nothing bound" and "nothing loaded" need different repairs, and only
        # the probe's own record can tell them apart
        from ..repro_tools import EXAMPLES_LABEL
        dead = ""
        for rev in ("buggy", "fixed"):
            obs = ((ctx.results.get(rev) or {}).get("probes") or {}).get(EXAMPLES_LABEL)
            if obs and not obs.get("n_records"):
                tail = (obs.get("traceback_tail") or "").strip().splitlines()
                dead = (f"; the example probe did not run at all on {rev} "
                        f"({obs.get('status')}): {tail[0][:200] if tail else ''}")
                break
        detail += dead or (f"; {result['n_unobserved']} never evaluated — the preamble does "
                           f"not bind everything they need: "
                           + "; ".join(result.get("unobserved", [])[:3]))
    return ok, detail


def _revisions_differ(ctx, milestone, observations):
    """The whole point, checked early enough to still act on it."""
    result = compare(ctx.results.get("buggy") or {}, ctx.results.get("fixed") or {}, ctx.spec)
    numeric = (result.get("numeric") or {}).get("verdict")
    if result["fail_to_pass"]:
        return True, "the reproducing test goes FAIL -> PASS"
    if numeric == FLIPPED:
        return True, "the numerical probe shows the defect only on buggy"
    return False, (f"no difference established (transition {result['transition']}, "
                   f"numeric {numeric})")


_CHECKS = {
    "steps_succeeded": _steps_succeeded,
    "test_ran": _test_ran,
    "test_fails": _test_is("fail"),
    "test_passes": _test_is("pass"),
    "probe_ran": _probe_ran,
    "probe_shows_defect": _probe_shows_defect,
    "probe_clean": _probe_clean,
    "revisions_differ": _revisions_differ,
    "examples_reproduced": _examples_reproduced,
}


def _tail(record: dict, limit: int = 200) -> str:
    text = (record.get("stderr") or record.get("stdout") or "").strip()
    return text[-limit:] if text else "(no output)"

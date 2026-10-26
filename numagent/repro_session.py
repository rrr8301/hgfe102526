from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Callable

from numbugminer import config as _config  # noqa: F401 — loads .env into os.environ on import

from . import repro_compare, repro_tools
from .repro_container import Container
from .repro_package import PackageOptions, package_reproduction
from .repro_specs import ReproSpec, python_for_era
from .repro_tools import RunContext
from .repro_workspace import BUGGY_DIR, FIXED_DIR, prepare_workspace


@dataclass
class AgentReproResult:
    """The outcome of one reproduction attempt, whatever produced it."""
    slug: str
    bug_type: str
    image: str
    status: str
    reasoning: str = ""                  # which reasoning strategy drove the tools
    rationale: str = ""
    transition: str = ""
    fail_to_pass: bool | None = None
    numeric_verdict: str | None = None
    examples_verdict: str | None = None
    # why the test could not run, when it could not: {"cause", "detail"}
    setup_cause: dict = field(default_factory=dict)
    # every probe's FULL code, {revision: {label: code}} — the log shows only a
    # preview, and an empty observation cannot be diagnosed without the source
    probe_code: dict = field(default_factory=dict)
    report: dict = field(default_factory=dict)
    comparison: dict = field(default_factory=dict)
    workspace: dict = field(default_factory=dict)
    benchmark_image: str = ""            # <repo>-<issue_id>, when packaged
    packaging: dict = field(default_factory=dict)
    iterations: int = 0
    history: list = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        import dataclasses
        return dataclasses.asdict(self)


def github_commit_date(repo: str, sha: str, token: str | None = None) -> str:
    """ISO committer date of `sha` in `repo`, from the GitHub API — or ""."""
    import json
    import urllib.request

    if not (repo and sha):
        return ""
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{sha}",
                                     headers={"User-Agent": "numagent-benchmark",
                                              **({"Authorization": f"token {token}"}
                                                 if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            return ((json.load(resp).get("commit") or {}).get("committer") or {}).get("date", "")
    except Exception:  # noqa: BLE001 — no date just means no cap
        return ""


def _python_for_the_commit(spec: ReproSpec, token: str | None) -> None:
    """Cap a Python instance's interpreter at what existed when it was fixed.

    The image is chosen before any clone, so the commit's date comes from the
    API. A mined Python newer than the commit — 3.14 for an April-2025 jax —
    has no wheels for that era's jaxlib or numpy, and fails before anything
    about the bug can be measured.
    """
    if (spec.language or "python") != "python":
        return
    era = github_commit_date(spec.repo, spec.fixed_commit or spec.buggy_commit, token)
    capped = python_for_era(spec.python_version, era)
    floor = getattr(spec, "python_floor", "")
    if floor and int(capped.split(".")[1]) < int(floor.split(".")[1]):
        capped = floor            # the era never drags it below what the project supports
    if capped != spec.python_version:
        spec.environment_notes = tuple(spec.environment_notes or ()) + (
            f"Python {capped}, not the mined {spec.python_version}: the fix was committed "
            f"{era[:10]}, before {spec.python_version} had wheels for its dependencies.",)
        spec.python_version = capped


def _session_cause(error: str) -> str:
    """The setup cause a session error names, when no comparison can."""
    if error.startswith("workspace prep failed"):
        return repro_compare.CAUSE_WORKSPACE_PREP_FAILED
    if "produced no executable" in error:          # PEVR milestones / Reflective steps
        return repro_compare.CAUSE_NO_EXECUTABLE_PLAN
    return repro_compare.CAUSE_DRIVER_ERROR


def _failed_builds(info: dict) -> tuple:
    """The revisions whose COMPILE failed, and the log line that says why.

    The compile's exit code decides, not the import check that follows it: that
    check imports the package by the repository's name, and a repository whose
    import name differs (scikit-learn is `sklearn`) would stop a healthy run.
    """
    build = (info or {}).get("build") or {}
    if not isinstance(build, dict) or "skipped" in build:
        return [], ""
    failed, detail = [], ""
    for rev in ("buggy", "fixed"):
        r = build.get(rev)
        if not isinstance(r, dict):
            continue
        if r.get("exit_code") not in (0, None) or (r.get("error") and not r.get("built")):
            failed.append(rev)
            detail = detail or repro_compare.first_error_line(
                r.get("output") or r.get("error") or "")
    return failed, detail


def build_result(ctx: RunContext, spec: ReproSpec, reasoning: str = "",
                 error: str = "", packaging: dict | None = None) -> AgentReproResult:
    """Assemble the result, finalizing deterministically if the strategy never
    called finalize_reproduction itself."""
    if ctx.report is None:
        ctx.report = repro_tools.finalize_reproduction(ctx)   # deterministic fallback
    r = ctx.report
    # Some setup failures happen where no comparison can see them — the
    # checkout, the planner, the driver itself. The comparison then only knows
    # that nothing ran, so the session's own error names the cause instead.
    if error and r.get("status") == repro_compare.SETUP_FAILURE and (
            (r.get("setup_cause") or {}).get("cause")
            in (None, repro_compare.CAUSE_NOTHING_RAN, repro_compare.CAUSE_UNKNOWN)):
        r["setup_cause"] = {"cause": _session_cause(error), "detail": error[:300]}
    pk = packaging or {}
    return AgentReproResult(
        slug=spec.slug, bug_type=spec.bug_type, image=spec.image,
        status=r.get("status", "AMBIGUOUS_RESULT"), reasoning=reasoning,
        rationale=r.get("rationale", ""), transition=r.get("transition", ""),
        fail_to_pass=r.get("fail_to_pass"), numeric_verdict=r.get("numeric_verdict"),
        examples_verdict=r.get("examples_verdict"),
        setup_cause=r.get("setup_cause") or {},
        probe_code={rev: {label: s.get("code", "") for label, s in scripts.items()}
                    for rev, scripts in (getattr(ctx, "probe_scripts", None) or {}).items()},
        report=r, comparison=ctx.comparison or {}, workspace=ctx.workspace_info,
        benchmark_image=pk.get("image", "") if pk.get("packaged") else "",
        packaging=pk,
        iterations=ctx.iterations, history=ctx.history, error=error)


def _workspace_from_image(container, image: str) -> dict:
    """Workspace facts for a session running against a packaged instance.

    A packaged image is a snapshot of a container in which both revisions were
    built and the transition was validated, so the provisioning questions are
    already answered — and answered more reliably than a fresh resolve would.
    The manifest records what that environment was; this reports it in the shape
    the rest of the session expects, and verifies the two revisions are actually
    present rather than trusting the tag.
    """
    info = {"buggy_dir": BUGGY_DIR, "fixed_dir": FIXED_DIR,
            "buggy_provisioned": False, "fixed_provisioned": False,
            "test_patch_applied": True, "test_deps": {}, "error": "",
            "prepared_image": image, "provisioned_by": "packaged image"}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        probe = container.run("test -d . && echo HAS || echo MISSING",
                              workdir=workdir, timeout_s=120)
        info[f"{rev}_provisioned"] = "HAS" in (probe.stdout or "")
    if not (info["buggy_provisioned"] and info["fixed_provisioned"]):
        info["error"] = (f"{image} does not hold both revisions at {BUGGY_DIR} "
                         f"and {FIXED_DIR}")
        return info
    meta = container.run("cat /benchmark/metadata.json 2>/dev/null || true",
                         workdir="/", timeout_s=120)
    try:
        manifest = json.loads((meta.stdout or "").strip() or "{}")
    except ValueError:
        manifest = {}
    if manifest:
        env = manifest.get("environment") or {}
        info["era"] = manifest.get("historical_date") or ""
        info["runtime"] = {"python": env.get("python"), "numpy": env.get("numpy"),
                           "scipy": env.get("scipy"), "blas": env.get("blas")}
        info["build"] = {"skipped": "already built in the packaged image"}
        info["instance_id"] = manifest.get("instance_id")
        info["validated_transition"] = (manifest.get("reproduction") or {}).get(
            "transition")
    return info


def run_session(spec: ReproSpec, *, driver: Callable[[RunContext], str],
                reasoning: str = "", work_dir: Path, token: str | None = None,
                cmd_timeout_s: float = 1800.0, keep_container: bool = False,
                package: PackageOptions | None = None, stream_output: bool = False,
                probes: bool = True, prepared_image: str = "",
                build_repair: bool = False, defer_build: bool = False,
                on_event=None) -> AgentReproResult:
    """Run one reproduction attempt: provision, drive, finalize, tear down.

    `driver` receives the prepared context and returns an error message ("" when
    it finished cleanly). A driver that raises is caught — the attempt is still
    finalized from whatever did run, so a crash yields evidence, not nothing.

    `stream_output` mirrors every line the container prints to the event stream
    as it is produced, rather than only in the recorded result.

    `prepared_image` runs against a packaged benchmark instance instead of
    provisioning from scratch: the image already holds both revisions, built, in
    the environment where the transition was validated. That matters beyond
    speed for BUG-REVEALING TEST GENERATION, whose premise is that the
    environment is FIXED and only the generated test varies — re-deriving the
    environment per run reintroduces the one variable the task holds constant,
    so a candidate that fails could be failing for the environment's reasons.
    The workspace facts are then read from the image's own
    `/benchmark/metadata.json` rather than produced by `prepare_workspace`.

    When `package` asks for it, the finished reproduction is snapshotted — after
    the verdict, before teardown — as the self-contained benchmark image
    ``<repo>-<issue_id>`` (see :mod:`numagent.repro_package`).
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    _python_for_the_commit(spec, token)
    container = Container(prepared_image or spec.image)
    ctx = RunContext(spec=spec, container=container, package=package,
                     default_timeout_s=cmd_timeout_s, on_event=on_event,
                     probes=probes)
    if stream_output:
        # every line the container prints, as it prints it — including the
        # pre-agentic clone/install, which is the longest silence in a run
        container.on_output = lambda stream, line: ctx.emit(
            "output", {"stream": stream, "line": line})
    error = ""
    try:
        # --- pre-agentic: clean container + both revisions provisioned -------
        container.start()
        if prepared_image:
            ctx.workspace_info = _workspace_from_image(container, prepared_image)
        else:
            ctx.workspace_info = prepare_workspace(spec, container, token=token,
                                                   host_work_dir=work_dir,
                                                   defer_build=defer_build)
        ctx.emit("workspace", ctx.workspace_info)
        failed, detail = _failed_builds(ctx.workspace_info)
        if not ctx.workspace_info.get("buggy_provisioned"):
            error = f"workspace prep failed: {ctx.workspace_info.get('error')}"
        elif (ctx.workspace_info.get("build") or {}).get("deferred"):
            # build-in-loop: the deterministic build was skipped. The agent's
            # first plan builds BOTH revisions from the strategy; if both build,
            # it runs the developer test; any build or test failure replans.
            ctx.workspace_info["build_repair_needed"] = {
                "revisions": ["buggy", "fixed"],
                "detail": "build deferred — Executor builds both revisions from the "
                          "strategy, then runs the developer test",
                "deferred": True}
            ctx.emit("thought", "build-in-loop: Executor builds buggy+fixed from the "
                                "strategy as the first plan, then runs the developer test")
            error = driver(ctx)
        elif failed and build_repair:
            # The caller owns the environment on this task, so a failed build is
            # work for the agent rather than a reason to stop: HBEC's search
            # variable IS the environment (E_B, E_F), so repairing a build is
            # the task and not a detour from it.
            #
            # HOW OFTEN THIS FIRES: rarely. Across 143 full-build reproductions
            # measured on 2026-10-14, zero revisions failed to compile — every
            # SETUP_ERROR in that arm built fine and failed later, at test
            # collection, where the agent already sees it and can replan. The
            # `__check_build` / `_version` import failures that look like build
            # breakage come from PREPARED IMAGES, which skip the build entirely,
            # so they never reach this branch either. Treat this as correctness
            # for the case where a compile does fail, not as a measured
            # improvement.
            #
            # The budget objection in the branch below still holds, so this is
            # not a free pass: the driver is told WHICH revision failed and what
            # the compiler said, the boundary still refuses installing a
            # released copy of the subject or uninstalling it, and the iteration
            # cap bounds the improvisation.
            ctx.workspace_info["build_repair_needed"] = {
                "revisions": failed,
                "detail": detail or "see the build log in workspace.build"}
            ctx.emit("thought",
                     f"build failed on {' and '.join(failed)} "
                     f"({detail or 'no error line'}) — handing it to the agent to "
                     f"repair, because the environment is this task's search "
                     f"variable")
            error = driver(ctx)
        elif failed:
            # A compiled package that did not build cannot be imported, so no
            # test and no probe can run — and an agent handed that revision
            # spends its budget improvising builds (and pinning past the
            # project's own constraints) before arriving at the same verdict.
            # Stop here, at no LLM cost, with the compiler's own reason.
            revisions = " and ".join(failed)
            ctx.report = repro_compare.setup_failure_report(
                repro_compare.CAUSE_BUILD_FAILED,
                f"{revisions}: {detail or 'see the build log in workspace.build'}",
                f"the {revisions} revision{'s' if len(failed) > 1 else ''} failed to "
                f"build, so the package under test cannot be imported — stopped "
                f"before the agent ran")
            ctx.report["workspace"] = ctx.workspace_info
            ctx.history.append({"early_stop": repro_compare.CAUSE_BUILD_FAILED,
                                "revisions": failed, "detail": detail})
            ctx.emit("thought", f"build failed on {revisions} ({detail or 'no error line'}) "
                                f"— stopping before the agent runs")
        else:
            error = driver(ctx)
    except Exception as e:  # noqa: BLE001 — a crash must still produce a report
        error = f"{type(e).__name__}: {e}"[:400]
    finally:
        # An agent that asked for the image already has one (the tool ran while
        # it was still reasoning). Otherwise, unless the caller left packaging
        # entirely to the agent's judgement, package here: the verdict first —
        # a crashed driver still gets one — then the snapshot, both while the
        # container is still alive.
        if package and package.enabled and not ctx.packaging.get("packaged") \
                and package.fallback:
            try:
                if ctx.report is None:
                    ctx.report = repro_tools.finalize_reproduction(ctx)
                ctx.packaging = package_reproduction(ctx, options=package, report=ctx.report)
            except Exception as e:  # noqa: BLE001 — never sink a finished run
                ctx.packaging = {"packaged": False, "image": "",
                                 "error": f"{type(e).__name__}: {e}"[:300]}
            ctx.emit("packaging", ctx.packaging)
        if not keep_container:
            container.stop()
    return build_result(ctx, spec, reasoning=reasoning, error=error,
                        packaging=ctx.packaging)

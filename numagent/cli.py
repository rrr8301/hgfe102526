from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import report as _report
from .pipeline import run_pipeline


def _cmd_package(args) -> int:
    """Rebuild benchmark images from a finished run's results JSON.

    Nothing here asks a model anything: the result already says which probes
    ran and what they recorded, and the spec says how to rebuild the two
    revisions. The container is provisioned exactly as a live run provisions it,
    so the image holds real revisions rather than a description of them.
    """
    import json
    from pathlib import Path

    from .repro_container import Container
    from .repro_package import PackageOptions, package_from_result
    from .repro_specs import specs_from_index
    from .repro_workspace import prepare_workspace

    raw = json.loads(Path(args.result).read_text(encoding="utf-8"))
    results = raw if isinstance(raw, list) else [raw]
    wanted = set(args.slug or [])
    results = [r for r in results if isinstance(r, dict) and r.get("slug")
               and (not wanted or r["slug"] in wanted)]
    if not results:
        print(f"no instances in {args.result}"
              + (f" matching {sorted(wanted)}" if wanted else ""))
        return 2

    from .repro_package import DEFAULT_STATUSES
    options = PackageOptions(
        enabled=True, tag=args.image_tag, prefix=args.image_prefix,
        statuses=() if args.package_any_status else DEFAULT_STATUSES)
    # the run's own PR number is the only thing that ties a result back to a spec
    prs = []
    for r in results:
        tail = str(r["slug"]).rsplit("_", 1)[-1]
        if tail.isdigit():
            prs.append(int(tail))
    specs = specs_from_index(args.index,
                             args.patches_dir or Path(args.index).parent,
                             strategies_path=args.strategies,
                             candidates_path=args.candidates,
                             only=prs or None)
    by_slug = {s.slug: s for s in specs}

    failures = 0
    for r in results:
        slug, status = r["slug"], r.get("status", "")
        spec = by_slug.get(slug)
        print(f"=== {slug}  [{status}]")
        if spec is None:
            print(f"    !! no spec for {slug} in {args.index} — "
                  f"is --candidates the right repository?")
            failures += 1
            continue
        if not options.wants(status):
            print(f"    skipped: status {status!r} is not packaged "
                  f"(use --package-any-status to override)")
            continue
        container = Container(spec.image)
        try:
            container.start()
            info = prepare_workspace(spec, container,
                                     host_work_dir=Path(args.work_dir))
            if not info.get("buggy_provisioned"):
                print(f"    !! workspace prep failed: {info.get('error')}")
                failures += 1
                continue
            out = package_from_result(r, spec, container, options=options)
            if out.get("packaged"):
                print(f"    packaged {out['image']} "
                      f"({out.get('n_probes')} probe(s), "
                      f"{out.get('n_probe_sources')} source(s)) -> "
                      f"docker run --rm {out['image']}")
            else:
                print(f"    !! not packaged: {out.get('error')}")
                failures += 1
        finally:
            if not args.keep_container:
                container.stop()
    return 1 if failures else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="numagent", description="Build & test numerical repos.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="detect, build, and test a repository")
    run_p.add_argument("src", help="git URL or local path to the repository")
    run_p.add_argument("--json", action="store_true", help="emit JSON instead of a summary")
    run_p.add_argument("--no-build", action="store_true")
    run_p.add_argument("--no-test", action="store_true")
    run_p.add_argument("--in-place", action="store_true", help="run in a local dir without copying")
    run_p.add_argument("--workdir", default=None, help="where to place the working copy")
    run_p.add_argument("--timeout", type=float, default=900.0, help="per-command timeout (s)")

    detect_p = sub.add_parser("detect", help="only detect language/build system")
    detect_p.add_argument("src")
    detect_p.add_argument("--json", action="store_true")

    agent_p = sub.add_parser("agent", help="drive an LLM agent to build & test the repo")
    agent_p.add_argument("src", nargs="?", default=None,
                         help="git URL or local path (optional if --context-pr supplies the repo)")
    agent_p.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    agent_p.add_argument("--model", default="", help="override the default model for the provider")
    agent_p.add_argument("--fix", action="store_true", help="enable the auto-fix loop (agent may patch source and iterate)")
    agent_p.add_argument("--docker", action="store_true", help="containerized mode: agent authors .numagent/Dockerfile + run.sh and runs build+test under Docker")
    agent_p.add_argument("--numeric", action="store_true", help="add numerical-correctness tools (tolerance / convergence / reproducibility)")
    # Context sourced from the numbugminer PR/issue data.
    agent_p.add_argument("--context-pr", default=None,
                         help="PR ref (URL / owner/repo#N); miner supplies the bug report + buggy commit as context")
    agent_p.add_argument("--context-file", default=None, help="read the task context from a file")
    agent_p.add_argument("--context", default=None, help="inline task context string")
    agent_p.add_argument("--leak-fix", action="store_true",
                         help="(ablation) also include the fix commits + diff in the context")
    agent_p.add_argument("--apply-test-patch", action="store_true",
                         help="preprocessing: apply the PR's test-file hunks onto the buggy "
                              "checkout so the reproducing test is present and failing before "
                              "the agent starts (fail→pass target; the fix is withheld)")
    agent_p.add_argument("--max-iterations", type=int, default=25)
    agent_p.add_argument("--in-place", action="store_true")
    agent_p.add_argument("--workdir", default=None)
    agent_p.add_argument("--quiet", action="store_true", help="don't stream tool activity")

    dockerize_p = sub.add_parser("dockerize", help="deterministic: generate .numagent/Dockerfile + run.sh and build+run under Docker (no LLM)")
    dockerize_p.add_argument("src", help="git URL or local path")
    dockerize_p.add_argument("--emit-only", action="store_true", help="write the artifacts but don't build/run")
    dockerize_p.add_argument("--in-place", action="store_true")
    dockerize_p.add_argument("--workdir", default=None)
    dockerize_p.add_argument("--timeout", type=float, default=1800.0)

    package_p = sub.add_parser("package", help="rebuild the benchmark image for a run that "
                               "already finished — replays the stored result, no LLM")
    package_p.add_argument("result", help="the run's results JSON (one or more instances)")
    package_p.add_argument("index", help="the same split-patches index.json the run used")
    package_p.add_argument("--patches-dir", default=None,
                           help="dir holding the split .source/.test patches (default: index's dir)")
    package_p.add_argument("--candidates", default=None,
                           help="the mined *_candidates.json (build commands / python version)")
    package_p.add_argument("--strategies", default=None,
                           help="the mined *_strategies.json")
    package_p.add_argument("--slug", action="append", default=[],
                           help="only these slugs from the results file; repeatable")
    package_p.add_argument("--image-prefix", default="",
                           help="registry/namespace prefix (e.g. 'numbench/')")
    package_p.add_argument("--image-tag", default="latest")
    package_p.add_argument("--package-any-status", action="store_true",
                           help="package even when the run did not reproduce the bug")
    package_p.add_argument("--work-dir", default="workspace/repro",
                           help="where to place the host checkout")
    package_p.add_argument("--keep-container", action="store_true")

    repro_p = sub.add_parser("repro", help="agentic containerized reproduction: an LLM agent "
                             "builds+tests the buggy repo in a clean Docker container from the "
                             "mined strategy and exposes the numerical bug (reproduce-only)")
    repro_p.add_argument("index", help="split-patches index.json (workspace/patches/index.json)")
    repro_p.add_argument("--patches-dir", default=None,
                         help="dir holding the split .source/.test patches (default: index's dir)")
    repro_p.add_argument("--strategies", default=None,
                         help="the mined *_strategies.json (supplies the reproduction strategy). "
                              "OMIT it for the no-strategy ablation: the agent then gets the "
                              "patch, the mined environment and the reproducing test's NAME, but "
                              "no plan, no expected behaviour and no input/output examples. Note "
                              "the verdict is then earned by the test transition alone, since "
                              "there are no reported values to check — compare the arms on "
                              "fail_to_pass, not on REPRODUCED")
    repro_p.add_argument("--candidates", default=None,
                         help="the mined *_candidates.json (build commands / python version)")
    repro_p.add_argument("--pr", type=int, action="append", default=None,
                         help="restrict to these PR number(s); repeatable. Several run one "
                              "after another, each into its own results file and trace "
                              "(pass --out to combine them into one file instead)")
    repro_p.add_argument("--provider", choices=["anthropic", "openai", "deepseek"],
                         default="anthropic")
    repro_p.add_argument("--model", default="", help="override the default model")
    repro_p.add_argument("--reasoning", choices=list(REASONING), default="react",
                         help="which reasoning strategy drives the shared toolset")
    repro_p.add_argument("--max-iterations", type=int, default=30,
                         help="reasoning turns (react) / executed plan steps (reflective)")
    repro_p.add_argument("--max-reflections", type=int, default=3,
                         help="(reflective) how many times the plan may be revised")
    repro_p.add_argument("--max-replans", type=int, default=3,
                         help="(pevr) how many times a failed verification may replan")
    repro_p.add_argument("--task", choices=["BRTG", "HBEC"], default="BRTG",
                         help="(pevr) reproduction boundary: BRTG (frozen environment, "
                              "generate a test) or HBEC (environment mutable, developer "
                              "test fixed)")
    repro_p.add_argument("--build-mode", choices=["fixed", "in-loop"], default="fixed",
                         help="(pevr) 'fixed' builds both revisions pre-agentically "
                              "(default); 'in-loop' defers the build so the Executor builds "
                              "buggy+fixed from the strategy, runs the developer test, and "
                              "replans on any failure (forces HBEC)")
    repro_p.add_argument("--cmd-timeout", type=float, default=1800.0, help="per-command timeout (s)")
    repro_p.add_argument("--work-dir", default=None, help="where to place the host checkout")
    repro_p.add_argument("--keep-container", action="store_true", help="don't remove the container after")
    repro_p.add_argument("--all-candidates", action="store_true",
                         help="include non-numerical candidates too (default: numerical only)")
    repro_p.add_argument("--package-image", action="store_true",
                         help="allow packaging: the agent may snapshot its reproduction as the "
                              "self-contained benchmark image <repo>-<issue_id> (both revisions, "
                              "both patches, the probe scripts, and a buggy-vs-fixed comparison "
                              "entry point), and one is packaged at the end of the session if it "
                              "did not")
    repro_p.add_argument("--no-auto-package", action="store_true",
                         help="leave packaging entirely to the agent's judgement — no image is "
                              "written unless the agent decides to package")
    repro_p.add_argument("--package-any-status", action="store_true",
                         help="package even when the bug did not reproduce (default: only "
                              "REPRODUCED / PARTIALLY_REPRODUCED)")
    repro_p.add_argument("--image-prefix", default="",
                         help="registry/namespace prefix for the packaged image "
                              "(e.g. 'numbench/' or 'ghcr.io/me/')")
    repro_p.add_argument("--image-tag", default="latest", help="tag for the packaged image")
    repro_p.add_argument("--out", default=None,
                         help=f"write per-issue results JSON here (default: "
                              f"{RESULTS_DIR}/<run>.json)")
    repro_p.add_argument("--log", default=None,
                         help=f"also write the console trace to this text file (default: "
                              f"{LOGS_DIR}/<run>.txt, named after the results file)")
    repro_p.add_argument("--quiet", action="store_true", help="don't stream the ReAct trace")
    repro_p.add_argument("--no-probes", "--test-only", action="store_true",
                         help="TEST ORACLE ONLY: generate and run no numerical "
                              "probe, do not evaluate the report's examples, and "
                              "stop as soon as the reproducing test is seen to "
                              "FAIL on buggy and PASS on fixed. The arm that "
                              "measures what the FAIL->PASS criterion accepts on "
                              "its own, and the cheapest way to run the set")
    repro_p.add_argument("--show-output", "--stream", action="store_true",
                         help="stream every line the container prints — builds, tests and "
                              "probes — live, as it runs, under the command that produced it")
    repro_p.add_argument("--output-lines", type=int, default=40,
                         help="cap on the after-the-fact dumps (a failed probe's traceback); "
                              "0 = print them in full")

    args = parser.parse_args(argv)

    if args.cmd == "agent":
        return _run_agent_cmd(args)

    if args.cmd == "package":
        return _cmd_package(args)
    if args.cmd == "repro":
        return _run_repro_cmd(args)

    if args.cmd == "dockerize":
        return _run_dockerize_cmd(args)

    if args.cmd == "detect":
        from . import detect as _detect
        from . import workspace
        from .models import RunReport

        path = args.src if not workspace._is_url(args.src) else workspace.prepare(args.src)
        det = _detect.detect(path)
        rep = RunReport(repo_path=path, detect=det)
        print(_report.as_json(rep) if args.json else _report.summary(rep))
        return 0

    report = run_pipeline(
        args.src,
        workdir=args.workdir,
        copy=not args.in_place,
        do_build=not args.no_build,
        do_test=not args.no_test,
        timeout_s=args.timeout,
    )
    print(_report.as_json(report) if args.json else _report.summary(report))
    return 0 if report.success else 1


#: Reasoning strategies that can drive the shared reproduction toolset. Each is
#: a package exporting ``reproduce(spec, ...) -> AgentReproResult``; adding one
#: is a new entry here and nothing else.
REASONING = ("react", "reflective", "pevr")


def _reasoning_strategy(name: str):
    """Import the named strategy's entry point (lazily — each pulls in an LLM SDK)."""
    if name == "react":
        from .ReAct import reproduce
        return reproduce
    if name == "reflective":
        from .Reflective import reproduce
        return reproduce
    if name == "pevr":
        from .PEVR import reproduce
        return reproduce
    raise SystemExit(f"unknown reasoning strategy {name!r} (expected one of {REASONING})")


#: Where a reproduction run leaves its two artefacts. Both are written by
#: default: a run whose trace exists only in a terminal cannot be read back, and
#: a verdict without the trace that produced it cannot be checked.
RESULTS_DIR = Path("workspace/reproducer_results")
LOGS_DIR = Path("workspace/reproducer_logs")


def _run_stem(args) -> str:
    """A name for this run: what it reproduced, how, and when.

    The timestamp is what keeps a re-run from overwriting the evidence for the
    previous one — the same instance is often run several times while an
    environment problem is being chased down.
    """
    import time

    prs = "-".join(str(p) for p in (args.pr or [])[:3])
    if args.pr and len(args.pr) > 3:
        prs += f"+{len(args.pr) - 3}"
    what = f"pr{prs}" if prs else Path(args.index).stem
    # WITH the repository: a PR number alone collides across repos — numba 10088
    # and statsmodels 10088 both wrote pr10088_pevr_<model>_<stamp>.json, and
    # only the record's `slug` inside said which repo it was.
    repo = _repo_named(args) or _repo_of(args.index, args.pr)
    if repo:
        what = f"{repo}_{what}"
    model = re.sub(r"[^\w.-]+", "-", args.model or "default").strip("-")
    return f"{what}_{args.reasoning}_{model}_{time.strftime('%Y%m%d-%H%M%S')}"


def _repo_named(args) -> str:
    """The repository the run's candidates/strategies file is for, from its name.

    `workspace/new-mine/numba__numba_candidates.json` -> "numba". Consulted
    BEFORE the index, which cannot separate numba 10088 from statsmodels 10088.
    """
    for path in (getattr(args, "candidates", "") or "",
                 getattr(args, "strategies", "") or ""):
        stem = Path(path).name
        m = re.match(r"([\w.-]+)__([\w.-]+?)_(?:candidates|mine)\b", stem)
        if m:
            return m.group(2)
    return ""


def _repo_of(index_path, prs) -> str:
    """The repository short name the selected PRs belong to ("" when mixed)."""
    import json

    try:
        entries = json.loads(Path(index_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    wanted = {int(p) for p in (prs or [])}
    repos = {str(e.get("repo") or "").rsplit("/", 1)[-1]
             for e in entries if isinstance(e, dict)
             and (not wanted or e.get("pr") in wanted)}
    repos.discard("")
    return re.sub(r"[^\w.-]+", "-", repos.pop()) if len(repos) == 1 else ""


def _run_artifacts(args):
    """(results path, log path) for this run, with their directories in place."""
    stem = Path(args.out).stem if args.out else _run_stem(args)
    out = Path(args.out) if args.out else RESULTS_DIR / f"{stem}.json"
    log = Path(args.log) if args.log else LOGS_DIR / f"{stem}.txt"
    for path in (out, log):
        path.parent.mkdir(parents=True, exist_ok=True)
    return out, log


class _Tee:
    """Write to a stream and a file at once.

    The reproduction trace is the record of what the agent did — every command,
    its streamed output, each verification — and it exists only on the terminal
    unless it is captured. Keeping it beside the results JSON means a run can be
    read back later without re-running it.
    """

    def __init__(self, stream, handle):
        self._stream, self._handle = stream, handle

    def write(self, text):
        self._stream.write(text)
        self._handle.write(text)
        return len(text)

    def flush(self):
        self._stream.flush()
        self._handle.flush()

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()


def _run_repro_cmd(args) -> int:
    """Run the reproduction, mirroring the console trace to its log file.

    Several ``--pr`` without ``--out`` run as that many single-PR runs, one after
    another: each gets its OWN results file and trace, named as if it had been
    run alone, so a batch leaves one pair of files per PR. An explicit
    ``--out``/``--log`` asks for one file, and keeps the combined run.
    """
    import copy
    import json

    prs = list(dict.fromkeys(args.pr or []))
    if len(prs) > 1 and not args.out and not args.log:
        rows, codes = [], []
        for n, pr in enumerate(prs, 1):
            one = copy.copy(args)
            one.pr = [pr]
            print(f"##### PR {pr}  ({n}/{len(prs)}) #####")
            codes.append(_run_repro_cmd(one))
            try:
                recs = json.loads(Path(one.out).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                recs = []
            statuses = ", ".join(r.get("status", "?") for r in recs) or "no instance run"
            rows.append((pr, statuses, one.out))
            print()
        print(f"batch: {len(prs)} PR(s), one results file each")
        for pr, statuses, out in rows:
            print(f"  #{pr:<7} {statuses:<22} {out}")
        return 0 if 0 in codes else 1

    out_path, log_path = _run_artifacts(args)
    args.out = str(out_path)          # the results file the run will write
    with open(log_path, "w", encoding="utf-8") as fh:
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(stdout, fh), _Tee(stderr, fh)
        try:
            code = _repro_cmd(args)
        finally:
            sys.stdout, sys.stderr = stdout, stderr
    print(f"trace   -> {log_path}")
    return code


def _repro_cmd(args) -> int:
    """Agentic containerized reproduction of mined numerical bugs."""
    import json
    from pathlib import Path

    from .agent.llm.base import build_client
    from .repro_package import DEFAULT_STATUSES, PackageOptions, image_name
    from .repro_specs import specs_from_index

    reproduce = _reasoning_strategy(args.reasoning)
    index_path = Path(args.index)
    patches_dir = Path(args.patches_dir) if args.patches_dir else index_path.parent
    work_dir = Path(args.work_dir) if args.work_dir else patches_dir.parent / "repro"
    work_dir.mkdir(parents=True, exist_ok=True)

    specs = specs_from_index(
        index_path, patches_dir, strategies_path=args.strategies,
        candidates_path=args.candidates, only=args.pr,
        numerical_only=not args.all_candidates)
    if not specs:
        print("no matching issues in the index"); return 1

    client = build_client(args.provider, args.model)
    # strategy-specific knobs, passed only to the strategies that declare them
    import inspect
    params = inspect.signature(reproduce).parameters
    extra = {k: getattr(args, k) for k in ("max_reflections", "max_replans",
                                           "task", "build_mode")
             if k in params}
    package = PackageOptions(
        enabled=bool(args.package_image), tag=args.image_tag, prefix=args.image_prefix,
        statuses=() if args.package_any_status else DEFAULT_STATUSES,
        fallback=not args.no_auto_package)
    print(f"reproducing {len(specs)} issue(s) with {client.name}/{client.model} "
          f"({args.reasoning} reasoning)"
          + (f"\npackaging each reproduction as <repo>-<issue_id>:{package.tag}"
             if package.enabled else "") + "\n")

    def dump_stream(label, text, full: bool = False):
        text = (text or "").rstrip()
        if not text:
            return
        lines = text.splitlines()
        if args.output_lines and not full and len(lines) > args.output_lines:
            print(f"      [{label}] … {len(lines) - args.output_lines} earlier line(s) elided")
            lines = lines[-args.output_lines:]
        else:
            print(f"      [{label}]")
        for line in lines:
            print(f"      | {line}")

    def make_hook(slug):
        def hook(kind, payload):
            if args.quiet:
                return
            if kind == "thought":
                # Printed in full: these carry the plan outline, the packaging
                # decision and its reasoning — the parts of a run a reader has to
                # judge afterwards. Continuation lines are indented to the bullet.
                lines = [ln for ln in str(payload).strip().splitlines() if ln.strip()]
                for i, line in enumerate(lines):
                    print(f"    · {line}" if i == 0 else f"      {line}")
            elif kind == "exec":
                # a header for the lines that are about to stream under it
                print(f"    $ [{payload.get('revision')}/{payload.get('purpose')}] "
                      f"{payload.get('command', '')}")
            elif kind == "test_output":
                # the tail is what matters: the failure block and pytest's own
                # summary line both live at the end of a run
                dump_stream(f"{payload.get('revision')}/test "
                            f"exit={payload.get('exit_code')}"
                            + ("  TIMED OUT" if payload.get("timed_out") else ""),
                            payload.get("output"))
            elif kind == "output":
                mark = "!" if payload.get("stream") == "stderr" else "|"
                print(f"      {mark} {payload.get('line', '')}")
            elif kind == "tool":
                name = payload.get("name", "")
                if name == "package_benchmark_image":
                    extra = (f"{payload.get('image')} packaged"
                             if payload.get("packaged")
                             else f"not packaged ({payload.get('error') or '?'})")
                else:
                    extra = payload.get("command") or payload.get("transition") \
                        or payload.get("status") or payload.get("label") \
                        or payload.get("revision") or ""
                print(f"    → {name}: {str(extra)[:160]}")
                # printed in FULL: an observation cut at 300 characters hides the
                # very value a reader is looking for (a covariance's last column,
                # the 120th case of a grid)
                if payload.get("expected") is not None:
                    print(f"      expected: {json.dumps(payload['expected'], default=str)}")
                if payload.get("observed") is not None:
                    print(f"      observed: {json.dumps(payload['observed'], default=str)}")
                acc = payload.get("accuracy") or {}
                if acc.get("passed") is not None:
                    print(f"      match: {'PASS' if acc['passed'] else 'FAIL'}"
                          f"  max_rel_err={acc.get('max_rel_err')}"
                          f"  max_abs_err={acc.get('max_abs_err')}")
                if payload.get("probe_status"):
                    print(f"      probe: status={payload['probe_status']}"
                          f"  lang={payload.get('language')}"
                          f"  records={payload.get('n_records')}"
                          f"  values={payload.get('n_values')}"
                          f" (from {payload.get('values_source')})")
                    if not payload.get("n_values"):
                        print(f"      why: {_probe_empty_hint(payload)}")
                    for reason in (payload.get("reasons") or [])[:4]:
                        print(f"      reason: {reason}")
                    if payload.get("probe_status") != "ok" and payload.get("traceback"):
                        dump_stream("traceback", payload["traceback"])
            elif kind == "workspace":
                deps = payload.get("test_deps") or {}
                print(f"    [workspace] buggy={payload.get('buggy_provisioned')} "
                      f"fixed={payload.get('fixed_provisioned')} "
                      f"test_patch_applied={payload.get('test_patch_applied')} "
                      f"test_deps={deps.get('installed') or deps.get('skipped') or deps.get('error')}")
                restored = (payload.get("test_files_restored") or {}).get("restored") or []
                if restored:
                    print(f"    [workspace] {len(restored)} binary test fixture(s) the patch "
                          f"could not carry were taken from the fixed commit; every file with "
                          f"test code was patched normally")
                sysp = payload.get("system_packages") or {}
                if sysp.get("installed"):
                    print(f"    [workspace] apt: {sysp['installed']}")
                if payload.get("era"):
                    print(f"    [workspace] era: {payload['era']} "
                          f"(dependencies resolved as of the fixed commit)")
                for rev, r in (payload.get("build") or {}).items():
                    if isinstance(r, dict):
                        print(f"    [workspace] build {rev}: built={r.get('built')} "
                              f"exit={r.get('exit_code')} "
                              f"imports_from={r.get('imports_from') or '-'}"
                              + (f" era_pins={r['era_pins']}" if r.get("era_pins") else ""))
                        # The build log itself. It used to live only in the
                        # results JSON, so a build that failed said "built=False"
                        # in the trace and nothing else — the compiler's reason
                        # was in a file nobody reading the log had open. A FAILED
                        # build prints in full: that is the whole diagnosis, and
                        # eliding it to the tail is how you lose the first error.
                        if r.get("output"):
                            dump_stream(f"build {rev}",
                                        r["output"],
                                        full=not r.get("built"))
                for rev, r in (payload.get("node_dependencies") or {}).items():
                    if isinstance(r, dict) and "installed" in r:
                        print(f"    [workspace] npm {rev}: installed={r['installed']}"
                              + (f" exit={r.get('exit_code')}" if not r["installed"] else ""))
                cargo = payload.get("cargo_workspace") or {}
                for edit in cargo.get("retargets") or []:
                    print(f"    [workspace] cargo: {edit['crate']} {edit['from']} -> "
                          f"{edit['to']} in {edit['manifest']} on BOTH revisions "
                          f"(yanked from crates.io); buggy resolves="
                          f"{cargo['buggy'].get('resolves')}, fixed resolves="
                          f"{cargo['fixed'].get('resolves')}")
                for rev in ("buggy", "fixed"):
                    why = (cargo.get(rev) or {}).get("reason") or ""
                    if why and why != "not a Cargo workspace":
                        print(f"    [workspace] cargo {rev}: {why}")
        return hook

    _MARK = {"REPRODUCED": "✓ REPRODUCED (FAIL→PASS)",
             "F2P_REPRODUCED": "✓ F2P REPRODUCED (test oracle only)",
             "PARTIALLY_REPRODUCED": "~ PARTIALLY_REPRODUCED",
             "NOT_REPRODUCED": "✗ NOT_REPRODUCED",
             "SETUP_FAILURE": "! SETUP_FAILURE",
             "INVALID_STRATEGY": "! INVALID_STRATEGY",
             "AMBIGUOUS_RESULT": "? AMBIGUOUS_RESULT"}

    results = []
    for spec in specs:
        print(f"=== {spec.slug}  ({spec.bug_type})  image={spec.image}"
              + (f"  -> {image_name(spec, package)}" if package.enabled else "") + " ===")
        before = dict(client.usage)
        res = reproduce(
            spec, client=client, work_dir=work_dir, max_iterations=args.max_iterations,
            cmd_timeout_s=args.cmd_timeout, keep_container=args.keep_container,
            package=package, stream_output=args.show_output,
            probes=not args.no_probes,
            on_event=make_hook(spec.slug), **extra)
        spent = {k: client.usage[k] - before[k] for k in
                 ("calls", "input_tokens", "output_tokens",
                  "cache_read_tokens", "cache_write_tokens", "cost_usd")}
        num = f", numeric={res.numeric_verdict}" if res.numeric_verdict else ""
        num += f", examples={res.examples_verdict}" if res.examples_verdict else ""
        print(f"  => {_MARK.get(res.status, res.status)}  "
              f"[{res.transition or '?'}{num}, {res.iterations} steps]")
        if res.rationale:
            print(f"     {res.rationale}")
        if res.setup_cause:
            detail = res.setup_cause.get("detail")
            print(f"     setup cause: {res.setup_cause.get('cause')}"
                  + (f" — {detail}" if detail else ""))
        if res.error:
            print(f"     error: {res.error}")
        if package.enabled:
            pk = res.packaging or {}
            if pk.get("packaged"):
                print(f"     benchmark image: {pk['image']} "
                      f"[{pk.get('n_probes', 0)} probe(s) under /benchmark]")
            else:
                print(f"     benchmark image: not packaged ({pk.get('error') or '?'})")
        print(f"     cost: ${spent['cost_usd']:.4f}  "
              f"[{spent['calls']} LLM calls, "
              f"{spent['input_tokens']:,} in / {spent['output_tokens']:,} out tokens]")
        print()
        record = res.to_dict()
        record["usage"] = {"model": client.model, **spent}
        results.append(record)

    # F2P_REPRODUCED counts as a success for the exit code and the summary: in a
    # `--no-probes` run it is the best verdict available, and a queue that treats
    # it as a failure would re-run every instance of that arm forever.
    n_repro = sum(1 for r in results
                  if r["status"] in ("REPRODUCED", "F2P_REPRODUCED"))
    from collections import Counter
    dist = dict(Counter(r["status"] for r in results))
    total_iters = sum(r.get("iterations") or 0 for r in results)
    total = client.usage
    print(f"summary: {n_repro}/{len(results)} REPRODUCED | statuses: {dist}")
    print(f"total iterations: {total_iters} over {len(results)} issue(s) "
          f"(avg {total_iters / max(len(results), 1):.1f})")
    print(f"total cost: ${total['cost_usd']:.4f}  "
          f"[{total['calls']} LLM calls, {total['input_tokens']:,} in / "
          f"{total['output_tokens']:,} out tokens, {client.model}]")
    if not total["cost_usd"] and total["calls"]:
        print("  (no price entry for this model — tokens counted, cost unpriced)")
    packaged = [r["packaging"]["image"] for r in results
                if (r.get("packaging") or {}).get("packaged")]
    if package.enabled:
        print(f"packaged {len(packaged)}/{len(results)} benchmark image(s)"
              + (": " + ", ".join(packaged) if packaged else ""))
    out = Path(args.out)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"results -> {out}")
    return 0 if n_repro else 1


def _probe_empty_hint(payload: dict) -> str:
    """Say why a probe produced no numbers.

    The causes look identical in the observation ('observed: []') and need
    completely different responses — from "fix the probe" to "nothing is wrong".
    """
    status = payload.get("probe_status")
    if status == "probe_error":
        return ("the probe itself could not run (bad import / missing file / typo) — "
                "the revision was never exercised; fix the probe, not the repo")
    if status == "environment_error":
        return ("the package under test is not importable in this revision — its build "
                "or install is broken; no rewrite of the probe can fix that")
    if status == "harness_error":
        tb = payload.get("traceback") or ""
        if "NUMAGENT_NO_TOOLCHAIN" in tb:
            return (f"the {payload.get('language')} toolchain is not in the image — "
                    "install it in a setup command, or the probe cannot run at all")
        if "no probe harness for language" in tb:
            return tb.strip()
        return ("the probe never reported: it failed to build, the toolchain is missing, "
                "or the process was killed — see the traceback")
    if status == "timeout":
        return "the probe timed out"
    if not payload.get("n_records"):
        return ("the probe never called probe(name, value) — nothing was recorded, "
                "and no float could be scraped from its stdout")
    # Records that are not numbers are NORMAL — a boolean property, a symbolic
    # expression, a fraction. They are compared between the revisions and against
    # the report's expected outputs exactly as numbers are. What they cannot do is
    # feed the numeric-only checks, and saying "probe a numeric form" as though
    # the probe were wrong sends an agent to rewrite a probe that worked.
    numeric_form = {
        "python": "probe('x', float(expr.evalf()))",
        "javascript": "probe('x', Number(value))",
        "typescript": "probe('x', Number(value))",
        "java": "Probe(\"x\", ((Number) value).doubleValue())",
        "go": "Probe(\"x\", float64(v))",
        "rust": "probe_scalar(\"x\", v as f64)",
    }.get(payload.get("language") or "", "probe('x', <the value as a float>)")
    return ("the recorded values are not numbers (a boolean, a symbolic expression, "
            "an object) — which is fine: they are still compared across revisions and "
            "against the reported expected outputs. Only the numeric checks "
            "(NaN/Inf, residual, tolerance against a reference) have nothing to score. "
            f"Record a numeric form as well if you want those, e.g. {numeric_form}")


def _run_dockerize_cmd(args) -> int:
    from . import detect as _detect
    from . import docker_gen, docker_run, workspace

    repo_path = args.src if (args.in_place and not workspace._is_url(args.src)) \
        else workspace.prepare(args.src, args.workdir)
    det = _detect.detect(repo_path)
    print(f"Detected: {det.language.value} / {det.build_system.value}")

    if args.emit_only:
        arts = docker_gen.generate(repo_path, det)
        out = docker_gen.write(arts, repo_path)
        print(f"Wrote {out}/Dockerfile and {out}/run.sh (image tag: {arts.image_tag})")
        return 0

    step, arts = docker_run.dockerized_pipeline(repo_path, det, timeout_s=args.timeout)
    out = str(_report_path(repo_path))
    print(f"Artifacts: {out} (image tag: {arts.image_tag})")
    for c in step.commands:
        print(f"  {'✓' if c.ok else '✗'} {c.command.split()[0]} ... exit={c.exit_code} [{c.duration_s}s]")
    if not step.success and step.commands:
        # surface the failing command's tail for quick diagnosis
        bad = next((c for c in step.commands if not c.ok), step.commands[-1])
        tail = (bad.stdout + bad.stderr).strip().splitlines()[-15:]
        print("  --- last output ---\n  " + "\n  ".join(tail))
    verdict = "PASS" if step.success else "FAIL"
    color = "32" if step.success else "31"
    extra = ""
    if step.tests_passed is not None or step.tests_failed is not None:
        extra = f" ({step.tests_passed or 0} passed, {step.tests_failed or 0} failed)"
    print(f"\n\033[{color}m=> {verdict}\033[0m{extra}")
    return 0 if step.success else 1


def _report_path(repo_path):
    from pathlib import Path
    from . import docker_gen
    return Path(repo_path) / docker_gen.OUT_DIR


def _resolve_context(args):
    """Return (context, src, ref, test_patch) from --context* flags.

    --context-pr pulls the issue+PR bug report from the miner and, when no src is
    given, also supplies the repo URL and the buggy commit to check out. With
    --apply-test-patch it also returns the PR's test-only patch to apply during
    the agent's preprocessing.
    """
    if args.context_pr:
        from numbugminer import agent_bridge, pr_source
        from numbugminer.config import load_config

        cfg = load_config()
        pr_ref = pr_source.parse_pr_ref(args.context_pr)
        bundle = pr_source.fetch_pr_bundle(pr_ref, token=cfg.github_token)
        task = agent_bridge.build_bug_task(
            bundle,
            include_commits=args.leak_fix,
            include_diff=args.leak_fix,
        )
        if not args.quiet:
            print(f"Miner context: {task.source_url}  "
                  f"buggy={task.buggy_commit[:12] or '?'} fixed={task.fixed_commit[:12] or '?'}  "
                  f"signals={', '.join(task.signals) or '(none)'}")
        src = args.src or task.repo_url
        # Only auto-checkout the buggy commit when we derived the repo ourselves.
        ref = task.buggy_commit if (not args.src and task.buggy_commit) else None
        test_patch = task.test_patch if args.apply_test_patch else None
        if args.apply_test_patch and not args.quiet:
            print(f"Preprocessing: will apply reproducing test patch "
                  f"({', '.join(task.test_files) or 'no test files found'})")
        return task.problem_statement, src, ref, test_patch

    if args.context_file:
        from pathlib import Path
        return Path(args.context_file).read_text(encoding="utf-8"), args.src, None, None

    return args.context, args.src, None, None


def _run_agent_cmd(args) -> int:
    from .agent import build_client, run_agent
    from .config import load_env, require_key

    def on_event(kind, payload):
        if args.quiet:
            return
        if kind == "assistant" and payload.strip():
            print(f"\n\033[36m[agent]\033[0m {payload.strip()}")
        elif kind == "tool_call":
            arg_str = ", ".join(f"{k}={v!r}" for k, v in payload.arguments.items())
            print(f"  \033[33m→ {payload.name}\033[0m({arg_str})")
        elif kind == "tool_result":
            name, result = payload
            first = result.strip().splitlines()[0] if result.strip() else ""
            print(f"    \033[90m{first[:100]}\033[0m")

    loaded = load_env()
    if loaded and not args.quiet:
        print(f"Loaded environment from {loaded}")

    # Resolve the task context (and, from a PR, the repo + buggy commit + test patch).
    try:
        context, src, ref, test_patch = _resolve_context(args)
    except Exception as e:
        print(f"error: could not build context: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if not src:
        print("error: no repository given (pass src or --context-pr)", file=sys.stderr)
        return 2

    try:
        key = require_key(args.provider)
        client = build_client(args.provider, model=args.model, api_key=key)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    mode = "docker" if args.docker else ("build+test+fix" if args.fix else "build+test")
    if args.numeric:
        mode += "+numeric"
    if context:
        mode += "+context"
    print(f"Provider: {client.name} / {client.model}  (mode: {mode})")
    if ref and not args.quiet:
        print(f"Checking out buggy commit {ref[:12]} of {src}")
    try:
        result = run_agent(
            src,
            client,
            workdir=args.workdir,
            copy=not args.in_place,
            fix=args.fix,
            docker=args.docker,
            numeric=args.numeric,
            max_iterations=args.max_iterations,
            on_event=on_event,
            context=context,
            ref=ref,
            test_patch=test_patch,
        )
    except Exception as e:
        print(f"\nerror: agent run failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 3

    if result.changes:
        print(f"\n\033[35mChanges applied ({len(result.changes)} file(s)):\033[0m")
        for ch in result.changes:
            flag = " \033[31m[TEST FILE]\033[0m" if ch.get("is_test") else ""
            print(f"  {ch['action']}: {ch['path']}{flag}")
            if not args.quiet and ch.get("diff"):
                print("\033[90m" + ch["diff"].rstrip() + "\033[0m")

    verdict = result.verdict.upper()
    color = "32" if verdict == "PASS" else "31"
    print(f"\n\033[{color}m=> {verdict}\033[0m ({result.iterations} iterations)")
    print(result.summary)
    return 0 if result.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
import re
import sys
from pathlib import Path

from . import boundaries as B
from . import diagnose as D
from . import verify as V

ROOT = Path(__file__).resolve().parent.parent

TASK_ALIAS = {"reproduction": B.HBEC, "repro": B.HBEC, "hbec": B.HBEC,
              "brtg": B.BRTG}


def _task(name: str) -> str:
    return TASK_ALIAS.get((name or "").strip().lower(), B.BRTG)


def _dump(label: str, text: str, *, full: bool = False, keep: int = 60) -> None:
    body = (text or "").rstrip()
    if not body:
        return
    lines = body.splitlines()
    if not full and len(lines) > keep:
        print(f"      [{label}] … {len(lines) - keep} earlier line(s) elided")
        lines = lines[-keep:]
    else:
        print(f"      [{label}]")
    for line in lines:
        print(f"      | {line}")


def _dump_workspace(payload: dict) -> None:
    deps = payload.get("test_deps") or {}
    print(f"  [workspace] buggy={payload.get('buggy_provisioned')} "
          f"fixed={payload.get('fixed_provisioned')} "
          f"test_patch_applied={payload.get('test_patch_applied')} "
          f"test_deps={deps.get('installed') or deps.get('skipped') or deps.get('error')}")
    if payload.get("error"):
        print(f"  [workspace] error: {payload['error']}")
    sysp = payload.get("system_packages") or {}
    if sysp.get("installed"):
        print(f"  [workspace] apt: {sysp['installed']}")
    if payload.get("era"):
        print(f"  [workspace] era: {payload['era']} "
              f"(dependencies resolved as of the fixed commit)")
    pins = (payload.get("runtime") or {}).get("era_pins") or {}
    if pins:
        print(f"  [workspace] era pins: {pins}")
    for rev, r in (payload.get("test_requirements") or {}).items():
        if isinstance(r, dict) and (r.get("files") or r.get("failed")):
            print(f"  [workspace] test-requirements {rev}: files={r.get('files')} "
                  f"whole={r.get('installed_whole')} failed={r.get('failed') or '-'}")

    build = payload.get("build") or {}
    if build.get("skipped"):
        print(f"  [workspace] build: {build['skipped']}")
    for rev, r in build.items():
        if not isinstance(r, dict):
            continue
        print(f"  [workspace] build {rev}: built={r.get('built')} "
              f"exit={r.get('exit_code')} imports_from={r.get('imports_from') or '-'}")
        for key in ("era_pins", "installed_for_build", "remedies", "dropped_pins",
                    "repinned_for_build", "test_extras", "sanitizer"):
            if r.get(key):
                print(f"      {key}: {r[key]}")
        _dump(f"build {rev}", r.get("output") or "", full=True)

    nd = payload.get("node_dependencies") or {}
    for rev, r in nd.items():
        if isinstance(r, dict) and "installed" in r:
            print(f"  [workspace] npm {rev}: installed={r['installed']} "
                  f"exit={r.get('exit_code')}")
            _dump(f"npm {rev}", r.get("output") or "", full=not r.get("installed"))
    cmake = payload.get("cmake") or {}
    for rev, r in cmake.items():
        if isinstance(r, dict) and "configured" in r:
            print(f"  [workspace] cmake {rev}: configured={r['configured']}")
            _dump(f"cmake {rev}", r.get("output") or "", full=not r.get("configured"))


def _line(value, limit: int = 300) -> str:
    if isinstance(value, (list, tuple)):
        return "; ".join(_line(v, limit) for v in value)[:limit]
    text = " ⏎ ".join(part.strip() for part in str(value or "").splitlines()
                      if part.strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _dump_inputs(spec, context_file) -> None:
    plan = dict(getattr(spec, "strategy", {}) or {})
    n = len(plan)
    print(f"  · STRATEGY ({spec.bug_type or 'uncategorised'}, {n} field(s)): "
          f"{_line(plan.get('summary')) or '(no strategy — this instance ran without one)'}")
    if plan:
        env = plan.get("environment") or {}
        i = 0
        if env:
            i += 1
            bits = []
            if env.get("python_version"):
                bits.append(f"python {env['python_version']}")
            for key in ("pip_packages", "system_packages", "build_commands"):
                if env.get(key):
                    bits.append(f"{key}={_line(env[key], 160)}")
            print(f"      {i}. environment — {'; '.join(bits) or 'unspecified'}")
            for why in (env.get("pinned_because") or [])[:4]:
                print(f"         pinned: {_line(why)}")
        i += 1
        print(f"      {i}. test_command — {_line(plan.get('test_command')) or '-'}")
        i += 1
        print(f"      {i}. success — {_line(plan.get('success_criterion')) or '-'}")
        for ex in (plan.get("input_output_examples") or [])[:4]:
            i += 1
            print(f"      {i}. example — {ex.get('input')} -> buggy "
                  f"{ex.get('buggy_output')} | expected {ex.get('expected_output')}")
        o = plan.get("oracle") or {}
        if o:
            i += 1
            print(f"      {i}. oracle — {o.get('kind')} {o.get('tolerance') or ''} "
                  f"| ref: {_line(o.get('reference_source'), 180) or '-'}")
        if plan.get("numerical_probe"):
            print(f"         probe (unused under f2p_only): "
                  f"{str(plan['numerical_probe'])[:120]}")
        for risk in (plan.get("risks") or [])[:4]:
            print(f"         risk: {_line(risk, 200)}")
    try:
        ctx = json.loads(Path(context_file).read_text(encoding="utf-8"))
    except Exception:
        ctx = {}
    if ctx:
        comp = (ctx.get("retrieval") or {}).get("completeness") or {}
        scores = "  ".join(
            f"{task} {rep.get('required_present')}/{rep.get('required_total')}"
            for task, rep in comp.items() if isinstance(rep, dict))
        iso = (ctx.get("retrieval") or {}).get("isolation") or {}
        gaps = sorted({g["path"] for rep in comp.values() if isinstance(rep, dict)
                       for g in (rep.get("required_missing") or [])})
        print(f"  · CONTEXT view={ctx.get('view')} withheld={ctx.get('withheld')} "
              f"| completeness {scores or '-'} | isolation ok={iso.get('ok')}"
              + (f" | gaps: {', '.join(gaps)}" if gaps else ""))


def _dump_plan(payload: dict) -> None:
    plan = payload.get("plan") or {}
    inf = payload.get("influence") or {}
    actions = plan.get("actions") or []
    label = "REPLANNER" if payload.get("replan_of") else "PLANNER"
    head = f"{label} ({len(actions)} action(s))"
    if payload.get("replan_of"):
        head += f" after {payload['replan_of']}"
    print(f"  · {head}: {_line(plan.get('objective')) or '-'}")
    if not actions:
        print("      (no actions)")
    for i, a in enumerate(actions, 1):
        step = a.get("step") or a.get("purpose") or ""
        print(f"      {i}. [{a.get('revision', '?')}/{a.get('purpose', '?')}] {_line(step)}")
        if a.get("command"):
            print(f"         $ {_line(a['command'], 400)}")
    grounded = inf.get("grounded") or []
    print(f"      evidence: {inf.get('n_cited', 0)} cited, {len(grounded)} grounded")
    for path in (plan.get("evidence_used") or [])[:8]:
        print(f"        {'✓' if path in grounded else '·'} {path}")
    for note in (plan.get("assumptions") or [])[:4]:
        print(f"      assumes: {_line(note)}")
    if payload.get("evidence_supplied"):
        print(f"      evidence handed to this plan: "
              f"{str(payload['evidence_supplied'])[:200]}")
    print(f"      success when: {_line(plan.get('success_criterion')) or '-'}")
    if plan.get("error"):
        print(f"      PLANNER ERROR: {plan['error']}")


def _dump_replan(payload: dict) -> None:
    print(f"  · DIAGNOSIS after iteration {payload.get('after_iteration')} "
          f"({payload.get('outcome')}): {payload.get('diagnosis')}")
    print(f"      do: {_line(payload.get('instruction'))}")
    if payload.get("forbids"):
        print(f"      forbidden: {', '.join(payload['forbids'])}")
    if payload.get("evidence_need"):
        print(f"      may request: {payload['evidence_need']}")
    moves = payload.get("moves") or {}
    if moves:
        print(f"      may change: {moves.get('may_change')} | must not change: "
              f"{moves.get('must_not_change')}")


def _tracer(slug: str):
    def hook(kind, payload):
        if kind == "thought":
            print(f"  · {payload}")
        elif kind == "plan":
            _dump_plan(payload or {})
        elif kind == "replan":
            _dump_replan(payload or {})
        elif kind == "test_output":
            print(f"  $ [{payload.get('revision')}/test] {payload.get('command')}")
            print(f"    exit={payload.get('exit_code')}"
                  + ("  TIMED OUT" if payload.get("timed_out") else ""))
            _dump(f"{payload.get('revision')}/test", payload.get("output") or "",
                  full=True)
        elif kind == "workspace":
            _dump_workspace(payload or {})
        elif kind == "tool":
            name = (payload or {}).get("name") if isinstance(payload, dict) else payload
            if name == "run_shell_command":
                if payload.get("refused"):
                    print(f"  $ [{payload.get('revision')}/{payload.get('purpose')}] "
                          f"{payload.get('command')}")
                    print(f"    ✗ REFUSED: {payload['refused']}")
                    return
                if payload.get("purpose") == "test":
                    return
                print(f"  $ [{payload.get('revision')}/{payload.get('purpose')}] "
                      f"{payload.get('command')}")
                print(f"    exit={payload.get('exit_code')}")
                for stream in ("stdout", "stderr"):
                    _dump(stream, payload.get(stream) or "", keep=30)
            elif name == "rebuild_revision":
                asked = f" with {', '.join(payload['pins'])}" if payload.get("pins") else ""
                got = ", ".join(f"{k}=={v}" for k, v in (payload.get("era_pins") or {}).items())
                print(f"  ⟳ REBUILD {payload.get('revision')} on Python "
                      f"{payload.get('python_version')}{asked}: "
                      + (f"ok (venv runs {payload.get('venv_python')}, "
                         f"{payload.get('runtime_s')}s; pinned {got or '-'})" if payload.get("built")
                         else f"FAILED {payload.get('error') or ''}"))
            elif name == "verify_numerical_outcome":
                v = payload.get("observed") or {}
                print(f"  ⇒ VERIFY {payload.get('status')}  valid={v.get('valid')}  "
                      f"{v.get('reason','')}")
            elif name == "finalize_reproduction":
                print(f"  ⇒ finalize: {payload.get('status')}")
            elif isinstance(payload, dict) and payload.get("refused"):
                print(f"  ✗ refused {name}: {payload['refused']}")
            else:
                print(f"  [tool] {name}")
    return hook


def _out_path(args, slug: str, n_instances: int) -> Path:
    if args.out and n_instances == 1:
        return Path(args.out)
    if args.out:
        out = Path(args.out)
        base = out if out.is_dir() or not out.suffix else out.parent
        return base / f"{slug}_numericpevr.json"
    return ROOT / "Extra" / "results" / f"{slug}_numericpevr.json"


def _log_path(args, slug: str) -> Path:
    if args.log:
        return Path(args.log)
    if args.out and len(args.pr or []) == 1 and not args.repo:
        out = Path(args.out)
        if out.parent.name == "results":
            return out.parent.parent / "logs" / f"{out.stem}.txt"
        return out.with_suffix(".txt")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    model = re.sub(r"[^A-Za-z0-9._-]", "-", str(args.model or "model"))
    task = (args.task or "run").lower()
    return (ROOT / "Extra" / "logs"
            / f"{slug}_pevr_{model}_{task}_{stamp}.txt")


def _context_for(args, spec) -> Path | None:
    if args.context:
        return Path(args.context)
    name = f"{spec.repo.replace('/', '__')}_{_pr_of(spec)}.context.json"
    path = Path(args.context_dir) / name
    return path if path.is_file() else None


_DECLINED = re.compile(r"insufficient evidence|cannot derive|no concrete|"
                       r"placeholder|unable to determine", re.I)


def _preflight(spec, context_file, task: str) -> str:
    import json as _json

    from NumericSpecificMiner import completeness as comp
    from NumericSpecificMiner.context import NumericalContext

    notes = []
    summary = str((getattr(spec, "strategy", {}) or {}).get("summary") or "")
    if _DECLINED.search(summary):
        notes.append("the miner DECLINED to write a strategy for this instance")
    try:
        ctx = NumericalContext.from_dict(
            _json.loads(Path(context_file).read_text(encoding="utf-8")))
        report = comp.assess(ctx, comp.BRTG if task == B.BRTG else comp.HBEC)
        got = report["required_total"] - len(report["required_missing"])
        if not got:
            notes.append(f"the context satisfies 0 of {report['required_total']} "
                         f"required {task} fields")
    except Exception:
        return ""
    return "; ".join(notes)


def _clock() -> dict:
    import os
    import resource
    import time

    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"wall": time.monotonic(),
            "epoch": time.time(),
            "cpu_self": me.ru_utime + me.ru_stime,
            "cpu_children": kids.ru_utime + kids.ru_stime,
            "max_rss_kb": max(me.ru_maxrss, kids.ru_maxrss),
            "pid": os.getpid()}


def _elapsed(before: dict, after: dict) -> dict:
    from datetime import datetime, timezone

    wall = after["wall"] - before["wall"]
    cpu = ((after["cpu_self"] - before["cpu_self"])
           + (after["cpu_children"] - before["cpu_children"]))
    return {
        "wall_s": round(wall, 2),
        "wall_min": round(wall / 60, 2),
        "cpu_s": round(cpu, 2),
        "cpu_self_s": round(after["cpu_self"] - before["cpu_self"], 2),
        "cpu_children_s": round(after["cpu_children"] - before["cpu_children"], 2),
        "cpu_fraction_of_wall": round(cpu / wall, 4) if wall else None,
        "max_rss_mb": round(after["max_rss_kb"] / 1024, 1),
        "started_utc": datetime.fromtimestamp(before["epoch"],
                                              timezone.utc).isoformat(),
        "finished_utc": datetime.fromtimestamp(after["epoch"],
                                               timezone.utc).isoformat(),
        "measured": True,
        "cpu_note": ("self + docker-CLI children only; container compute is the "
                     "docker daemon's child and is not attributable here"),
    }


def _pr_of(spec) -> int:
    tail = str(spec.slug).rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def _selected(args) -> list | None:
    prs = [int(p) for p in (args.pr or [])]
    return prs or None


def cmd_run(args) -> int:
    from numagent.repro_specs import specs_from_index

    from .loop import GRADING, reproduce

    specs = specs_from_index(args.index,
                             args.patches_dir or Path(args.index).parent,
                             strategies_path=args.strategies,
                             candidates_path=args.candidates,
                             only=_selected(args))
    if args.repo:
        want = args.repo.lower()
        specs = [s for s in specs
                 if want in s.repo.lower() or want in s.repo.split("/")[-1].lower()]
    runnable, skipped, undermined = [], [], []
    for spec in specs:
        ctx_file = _context_for(args, spec)
        if not ctx_file:
            skipped.append(spec.slug)
            continue
        warning = _preflight(spec, ctx_file, _task(args.task))
        if warning:
            undermined.append((spec.slug, warning))
            if args.skip_undermined:
                continue
        runnable.append(spec)
    if not runnable:
        print(f"error: nothing to run — {len(specs)} instance(s) matched, "
              f"{len(skipped)} without a context in {args.context_dir}",
              file=sys.stderr)
        return 1
    if skipped:
        print(f"note: skipping {len(skipped)} instance(s) with no mined context: "
              f"{', '.join(skipped[:8])}" + (" …" if len(skipped) > 8 else ""),
              file=sys.stderr)
    for slug, why in undermined:
        verb = "SKIPPED" if args.skip_undermined else "running anyway"
        print(f"note: {slug} is under-mined for this task ({verb}) — {why}",
              file=sys.stderr)

    os.environ["NUMBUGBENCH_ERA_MODE"] = args.era_mode

    work_dir = Path(args.work_dir) if args.work_dir else ROOT / "workspace" / "numeric_pevr"
    work_dir.mkdir(parents=True, exist_ok=True)

    task = _task(args.task)
    grading = args.grading if args.grading != "auto" else GRADING.get(task)
    from numagent.cli import _Tee

    from .package import PackageOptions
    pkg = PackageOptions(enabled=args.package, prefix=args.image_prefix,
                         tag=args.image_tag,
                         artifacts_dir=args.artifacts_dir or "")

    print(f"reproducing {len(runnable)} instance(s)  task={task}  grading={grading}",
          file=sys.stderr)
    results, tally = [], {}
    for spec in runnable:
        log = _log_path(args, spec.slug)
        log.parent.mkdir(parents=True, exist_ok=True)
        handle = log.open("w", encoding="utf-8")
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout = _Tee(saved_out, handle)
        sys.stderr = _Tee(saved_err, handle)
        print(f"=== {spec.slug}  task={task}  grading={grading}  "
              f"image={args.from_image or spec.image}"
              f"{' (prepared)' if args.from_image else ''} ===", file=sys.stderr)
        _dump_inputs(spec, _context_for(args, spec))
        t_before = _clock()
        res = reproduce(spec, context_file=str(_context_for(args, spec)),
                        task=task, grading=grading, build_mode=args.build_mode,
                        package=pkg,
                        prepared_image=args.from_image,
                        on_event=_tracer(spec.slug),
                        provider=args.provider, model=args.model,
                        work_dir=work_dir, max_iterations=args.max_iterations,
                        evidence_budget=args.evidence_budget,
                        cmd_timeout_s=args.cmd_timeout,
                        keep_container=args.keep_container,
                        stream_output=args.show_output,
                        embed_provider=args.embed_provider,
                        embed_model=args.embed_model, chroma_dir=args.chroma_dir)
        timing = _elapsed(t_before, _clock())
        timing["in_container_s"] = round(
            sum(float(h.get("runtime_s") or 0)
                for h in (getattr(res, "history", None) or [])
                if isinstance(h, dict)), 2)
        try:
            res.timing = timing
        except Exception:
            pass
        if res.report is not None:
            res.report["timing"] = timing
            if isinstance(res.report.get("numeric_pevr"), dict):
                res.report["numeric_pevr"]["timing"] = timing
        summary = (res.report or {}).get("numeric_pevr", {})
        tok = (summary.get("tokens") or {}).get("total") or {}
        print(f"  => {res.status}  [{res.transition or '?'}] "
              f"final={summary.get('final')} iterations={summary.get('iterations')} "
              f"(replans={summary.get('replans')}) "
              f"evidence_calls={summary.get('evidence_calls')} "
              f"refusals={summary.get('refusals')}", file=sys.stderr)
        if tok:
            print(f"     tokens: {tok.get('input_tokens', 0):,} in / "
                  f"{tok.get('output_tokens', 0):,} out over {tok.get('calls', 0)} "
                  f"call(s)  ${tok.get('cost_usd', 0):.4f}", file=sys.stderr)
        print(f"     time: {timing['wall_min']:.1f} min wall  "
              f"({timing['in_container_s']:.0f}s in-container, "
              f"{timing['cpu_s']:.0f}s cpu, "
              f"{timing['max_rss_mb']:.0f} MB peak)", file=sys.stderr)
        if summary.get("verdicts"):
            print(f"     verdicts: {' -> '.join(summary['verdicts'])}", file=sys.stderr)
        if summary.get("diagnoses"):
            print(f"     diagnoses: {', '.join(summary['diagnoses'])}", file=sys.stderr)
        pkg_info = (res.report or {}).get("numbugbench_package") or {}
        if pkg_info.get("packaged"):
            print(f"     packaged: {pkg_info['image']}"
                  + (f"  [{pkg_info['image_size_gb']} GB]" if pkg_info.get("image_size_gb") else "")
                  + (f"  (+ {pkg_info['artifacts']})" if pkg_info.get("artifacts") else ""),
                  file=sys.stderr)
        elif args.package:
            print(f"     not packaged: {pkg_info.get('reason', 'unknown')}",
                  file=sys.stderr)
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        sys.stdout, sys.stderr = saved_out, saved_err
        handle.close()
        print(f"     log -> {log}", file=sys.stderr)
        results.append(res)
        tally[res.status] = tally.get(res.status, 0) + 1
        out = _out_path(args, spec.slug, len(runnable))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res.to_dict() if hasattr(res, "to_dict")
                                  else res.__dict__, indent=1, default=str),
                       encoding="utf-8")
        print(f"     -> {out}", file=sys.stderr)

    if len(results) > 1:
        print("\n--- " + f"{len(results)} instance(s)", file=sys.stderr)
        for status, n in sorted(tally.items(), key=lambda kv: -kv[1]):
            print(f"  {n:3d}  {status}", file=sys.stderr)
        earned = sum(n for st, n in tally.items()
                     if st in ("F2P_REPRODUCED", "REPRODUCED"))
        print(f"  reproduced {earned}/{len(results)} "
              f"({earned / len(results):.0%})", file=sys.stderr)
    return 0 if results else 1


def cmd_verify(args) -> int:
    data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    data = data[0] if isinstance(data, list) else data
    cmp_ = data.get("comparison") or {}
    task = _task(args.task)
    verdict = V.classify(cmp_.get("buggy"), cmp_.get("fixed"))
    print(json.dumps(verdict.as_dict(), indent=1))
    from .loop import F2P_ONLY, GRADING
    if GRADING.get(task) == F2P_ONLY:
        print(f"\nverdict under f2p_only grading: "
              f"{'F2P_REPRODUCED' if verdict.reproduction == V.F2P else 'NOT_REPRODUCED'}"
              f"  ({verdict.reproduction})")
    action = D.diagnose(verdict, task=task, context=None)
    print(f"\ndiagnosis: {action.diagnosis}\n  do: {action.instruction}")
    if action.forbids:
        print(f"  forbidden: {', '.join(action.forbids)}")
    if action.evidence_need:
        print(f"  would request: {action.evidence_need}")
    return 0


def cmd_refresh(args) -> int:
    from .package import refresh_scripts

    bad = 0
    for image in args.images:
        out = refresh_scripts(image, artifacts_dir=args.artifacts_dir or "")
        if out.get("refreshed"):
            print(f"  refreshed {image}  ({out.get('instance')})")
        else:
            bad += 1
            print(f"  ! {image}: {out.get('reason')}", file=sys.stderr)
    return 1 if bad else 0


def cmd_explain(args) -> int:
    print("== task boundaries (Modification 5)")
    for task, b in B.BOUNDARIES.items():
        print(f"\n{task}")
        print(f"  search variable : {b.search_variable}")
        print(f"  fixed           : {b.fixed}")
        print(f"  allowed         : {', '.join(b.allowed)}")
        print(f"  forbidden       : {', '.join(b.forbidden)}")
    print("\n== grading per task")
    from .loop import GRADING
    for task, mode in GRADING.items():
        print(f"  {task:5s} -> {mode}"
              + ("  (test transition alone; no numerical probe; "
                 "F2P_REPRODUCED / NOT_REPRODUCED)" if mode == "f2p_only"
                 else "  (transition + numerical probes)"))
    print("\n== outcome classes (Modification 2)")
    for name in (V.F2P, V.P2P, V.F2F, V.P2F, V.SETUP_ERROR, V.INVALID):
        print(f"  {name}")
    print(f"  bug-exposing failure types: {', '.join(V.BUG_EXPOSING)}")
    print(f"  setup failure types       : {', '.join(V.SETUP_KINDS)}")
    print("\n== diagnosis -> replanning action (Modification 3)")
    for name, action in D.POLICY.items():
        print(f"\n  {name}")
        print(f"    do        : {action.instruction}")
        if action.forbids:
            print(f"    forbidden : {', '.join(action.forbids)}")
        if action.evidence_need:
            print(f"    may ask   : {action.evidence_need}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="NumericPEVR", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="reproduce one instance")
    r.add_argument("index", help="split-patches index.json")
    r.add_argument("--pr", type=int, action="append", default=None,
                   help="restrict to these PR number(s); repeatable. Omit to run "
                        "every instance that has a mined context")
    r.add_argument("--repo", default="",
                   help="restrict to one repository, by name or owner/name "
                        "(e.g. jax, jax-ml/jax)")
    r.add_argument("--context", default=None,
                   help="one *.context.json — for a single instance. A run over "
                        "several resolves each context from --context-dir")
    r.add_argument("--context-dir", default=str(ROOT / "Strategy_numeric"),
                   help="where the miner's *.context.json files live")
    r.add_argument("--task", default="reproduction",
                   choices=tuple(TASK_ALIAS) + (B.HBEC, B.BRTG),
                   help="reproduction (= HBEC: rebuild the environment, graded on "
                        "the test transition alone) or brtg (= generate the test, "
                        "graded on the transition plus the numerical probes)")
    r.add_argument("--grading", default="auto",
                   choices=("auto", "f2p_only", "full"),
                   help="auto follows the task: f2p_only for reproduction, full "
                        "for brtg. f2p_only writes no numerical probe and reports "
                        "F2P_REPRODUCED or NOT_REPRODUCED")
    r.add_argument("--build-mode", default="fixed", choices=("fixed", "in-loop", "agent"),
                   help="fixed builds both revisions pre-agentically (default); "
                        "in-loop defers the build so the Executor builds buggy+fixed "
                        "from the strategy, runs the developer test, and replans on "
                        "any failure (forces HBEC / f2p_only)")
    r.add_argument("--patches-dir", default=None,
                   help="dir holding the split .source/.test patches "
                        "(default: the index's own dir)")
    r.add_argument("--strategies", default=None)
    r.add_argument("--candidates", default=None)
    r.add_argument("--provider", default="openai")
    r.add_argument("--model", default="")
    r.add_argument("--max-iterations", type=int, default=7,
                   help="PLAN/EXECUTE/VERIFY rounds. The first is the "
                        "initial plan, so the replan budget is one less: "
                        "the default 7 allows 6 replans. Raised from 4 "
                        "because a BRTG iteration that only measures now "
                        "consumes a round without being graded, so the old "
                        "cap left as few as one real attempt.")
    r.add_argument("--era-mode", default="static",
                   choices=("static", "dynamic", "off"),
                   help="which packages get era-pinned. static: the five names "
                        "in repro_workspace.ERA_PINNED, for every instance — "
                        "what every arm before this flag ran with. dynamic: the "
                        "requirements the mined strategy names for THIS instance, "
                        "with its own specifiers honoured, falling back to static "
                        "when the strategy names none. off: the ABLATION — no era "
                        "pins, no era-matched pytest plugins, no interpreter cap, "
                        "and the mined era_constraints withheld from the plan")
    r.add_argument("--skip-undermined", action="store_true",
                   help="do not run instances whose context satisfies none of "
                        "the task's required fields, or whose strategy the "
                        "miner declined to write. Off by default: such a run "
                        "is cheap, can still succeed, and excluding it "
                        "silently biases the reported rate")
    r.add_argument("--evidence-budget", type=int, default=2,
                   help="targeted retrieve_evidence calls allowed per run")
    r.add_argument("--cmd-timeout", type=float, default=1800.0)
    r.add_argument("--keep-container", action="store_true")
    r.add_argument("--show-output", action="store_true")
    r.add_argument("--embed-provider", default="ollama")
    r.add_argument("--embed-model", default="")
    r.add_argument("--chroma-dir", default=None)
    r.add_argument("--work-dir", default=None)
    r.add_argument("--from-image", default="",
                   help="run against a packaged instance image instead of "
                        "provisioning from scratch (e.g. numbugbench/scipy-19933:"
                        "latest). BRTG holds the environment fixed, and the image "
                        "IS the environment the transition was validated in")
    r.add_argument("--package", action="store_true",
                   help="package each validated F2P as a NumBugBench Docker "
                        "instance (both revisions + the frozen environment + "
                        "the developer test + runner scripts)")
    r.add_argument("--image-prefix", default="numbugbench/")
    r.add_argument("--image-tag", default="latest")
    r.add_argument("--artifacts-dir", default=None,
                   help="also write Dockerfile, lockfiles, scripts and "
                        "metadata.json here, beside the image")
    r.add_argument("--out", default=None)
    r.add_argument("--log", default=None,
                   help="the full trace — every command and its output. Defaults "
                        "to --out's path with a .txt suffix, else "
                        "Extra/logs/<slug>_pevr_<model>_<task>_<timestamp>.txt "
                        "— the timestamp is what keeps a re-run from "
                        "overwriting the previous run's trace")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("verify", help="re-classify a finished numagent result")
    v.add_argument("result")
    v.add_argument("--task", default="reproduction",
                   choices=tuple(TASK_ALIAS) + (B.HBEC, B.BRTG))
    v.set_defaults(func=cmd_verify)

    rf = sub.add_parser("refresh", help="update /benchmark/scripts inside existing "
                                       "instance images (no rebuild, no re-run)")
    rf.add_argument("images", nargs="+", help="e.g. numbugbench/scipy-19933:latest")
    rf.add_argument("--artifacts-dir", default=None,
                    help="also refresh the host-side copies here")
    rf.set_defaults(func=cmd_refresh)

    e = sub.add_parser("explain", help="print the boundary / outcome / policy tables")
    e.set_defaults(func=cmd_explain)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

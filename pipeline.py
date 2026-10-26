from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = str(ROOT / ".venv" / "bin" / "python")

#: the four the miner accepts
CATEGORIES = ("accuracy", "special_value", "correctness", "convergence")

#: model name -> provider. Checked in order, first substring wins.
PROVIDERS = (("claude", "anthropic"), ("haiku", "anthropic"), ("sonnet", "anthropic"),
             ("opus", "anthropic"), ("gpt", "openai"), ("o1", "openai"),
             ("o3", "openai"), ("deepseek", "deepseek"))

#: which API key each provider needs present before a stage that uses it runs
KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY", "ollama": ""}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def provider_for(model: str) -> str:
    low = (model or "").lower()
    for needle, provider in PROVIDERS:
        if needle in low:
            return provider
    return "ollama"


def parse_pr(url: str) -> tuple[str, str, int]:
    """(owner, repo, number) from a GitHub PR URL or owner/repo#N."""
    m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url) \
        or re.match(r"^([^/]+)/([^#]+)#(\d+)$", url.strip())
    if not m:
        raise SystemExit(f"error: cannot read a PR reference out of {url!r}")
    return m.group(1), m.group(2), int(m.group(3))


def category_from_csv(path: str, repo: str, number: int) -> str:
    import csv

    def cell(row: dict, *names) -> str:
        lower = {(k or "").strip().lower(): v for k, v in row.items()}
        for n in names:
            if lower.get(n):
                return str(lower[n])
        return ""

    for row in csv.DictReader(Path(path).open(encoding="utf-8")):
        m = re.search(r"/pull/(\d+)", cell(row, "pr-link", "pr_link", "prlink"))
        if not m or int(m.group(1)) != number:
            continue
        if cell(row, "repo").strip() not in ("", repo):
            continue
        return cell(row, "category", "bug_type", "bug type").strip().lower().replace(" ", "_")
    return ""


class Stage:
    """One pipeline step, with the file that proves it already ran."""

    def __init__(self, name: str, argv: list, produces: Path, *, env=None):
        self.name, self.argv, self.produces, self.env = name, argv, produces, env or {}


def run(stage: Stage, *, force: bool, dry: bool, log_dir: Path) -> dict:
    """Run one stage. Returns its record for the manifest."""
    rec = {"stage": stage.name, "command": " ".join(shlex.quote(a) for a in stage.argv),
           "produces": str(stage.produces)}
    if stage.produces.exists() and not force:
        print(f"  [skip] {stage.name}: {stage.produces} exists")
        return {**rec, "status": "skipped", "seconds": 0.0}
    if dry:
        print(f"  [dry ] {stage.name}: {rec['command']}")
        return {**rec, "status": "dry-run", "seconds": 0.0}

    log = log_dir / f"{stage.name}.log"
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"  [run ] {stage.name} -> {log}")
    env = {**os.environ, **stage.env}
    start = time.time()
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.run(stage.argv, cwd=ROOT, env=env, stdout=fh,
                              stderr=subprocess.STDOUT)
    took = time.time() - start
    ok = proc.returncode == 0 and stage.produces.exists()
    print(f"  [{'  ok' if ok else 'FAIL'}] {stage.name}  exit={proc.returncode}  "
          f"{took:.1f}s" + (f" ({took/60:.1f} min)" if took >= 120 else ""))
    if not ok:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]:
            print(f"         | {line[:160]}")
    return {**rec, "status": "ok" if ok else "failed", "exit": proc.returncode,
            "seconds": round(took, 1), "log": str(log)}


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        description="PR link + model -> mined evidence, strategy, and a graded "
                    "reproduction verdict.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python pipeline.py https://github.com/sympy/sympy/pull/26843 \\\n"
               "      --model claude-haiku-5-5 --category correctness\n"
               "  python pipeline.py https://github.com/sympy/sympy/pull/26843 \\\n"
               "      --model gpt-5.4-nano --categories annotations.csv\n")
    p.add_argument("pr", help="PR URL, or owner/repo#N")
    p.add_argument("--model", required=True,
                   help="drives every stage that calls an LLM "
                        "(claude-haiku-5-5, gpt-5.4-nano, deepseek-v4-flash, …)")
    p.add_argument("--provider", default="",
                   help="inferred from --model when omitted")
    p.add_argument("--category", default="", choices=("",) + CATEGORIES,
                   help="the ANNOTATED category. Required unless --categories "
                        "supplies it: the miner needs it before it mines and does "
                        "not classify")
    p.add_argument("--categories", default="",
                   help="annotation CSV (repo, pr-link|pr_link, category) to read "
                        "the category from")
    p.add_argument("--task", default="reproduction",
                   choices=("reproduction", "brtg"),
                   help="reproduction (HBEC) rebuilds the environment so the "
                        "developer test flips; brtg generates the test instead")
    p.add_argument("--out-dir", default="",
                   help="everything this run produces (default: "
                        "pipeline-results/<slug>_<model>)")
    p.add_argument("--max-iterations", type=int, default=7)
    p.add_argument("--evidence-budget", type=int, default=2)
    p.add_argument("--cmd-timeout", type=float, default=1800.0)
    p.add_argument("--era-mode", default="static",
                   choices=("static", "dynamic", "off"),
                   help="off is the no-historical-environment-guidance ablation")
    p.add_argument("--grading", default="auto", choices=("auto", "f2p_only", "full"))
    p.add_argument("--package", action="store_true",
                   help="package each validated F2P as a NumBugBench Docker "
                        "instance (both revisions + frozen environment + the "
                        "developer test + runner scripts)")
    p.add_argument("--image-prefix", default="numbugbench/")
    p.add_argument("--image-tag", default="latest")
    p.add_argument("--artifacts-dir", default="",
                   help="also write Dockerfile, lockfiles, scripts and "
                        "metadata.json here, beside the image")
    p.add_argument("--build-mode", default="fixed", choices=("fixed", "in-loop"),
                   help="fixed builds both revisions before the agent plans "
                        "(default); in-loop defers the build so the agent builds "
                        "both revisions from the strategy and replans on failure "
                        "(forces HBEC / f2p_only)")
    p.add_argument("--force", action="store_true",
                   help="re-run every stage even if its output exists")
    p.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    p.add_argument("--stop-after", default="",
                   choices=("", "split", "mine", "reproduce"))
    args = p.parse_args()

    owner, repo, number = parse_pr(args.pr)
    full = f"{owner}/{repo}"
    slug = f"{repo}_{number}"
    prefix = f"{owner}__{repo}"
    provider = args.provider or provider_for(args.model)
    model_tag = re.sub(r"[^A-Za-z0-9._-]", "-", args.model)

    # ---- the category, before anything is fetched or spent ---------------- #
    category, source = args.category, "--category"
    if not category and args.categories:
        if not Path(args.categories).is_file():
            print(f"error: no such CSV: {args.categories}", file=sys.stderr)
            return 2
        category = category_from_csv(args.categories, full, number)
        source = args.categories
        if not category:
            print(f"error: {full}#{number} is not in {args.categories}, or its "
                  f"`category` cell is empty", file=sys.stderr)
            return 2
    if not category:
        print("error: no category. Pass --category {" + "|".join(CATEGORIES)
              + "} or --categories <annotation.csv>.", file=sys.stderr)
        return 2
    if category not in CATEGORIES:
        print(f"error: category {category!r} (from {source}) is not one of "
              f"{CATEGORIES}", file=sys.stderr)
        return 2

    base = Path(args.out_dir) if args.out_dir else \
        ROOT / "pipeline-results" / f"{slug}_{model_tag}"
    patches, mined, logs = base / "patches", base / "mined", base / "logs"

    key = KEYS.get(provider, "")
    print("=== NumBugBench pipeline ===")
    print(f"  PR        {full}#{number}  ({args.pr})")
    print(f"  model     {args.model}   provider {provider}")
    print(f"  category  {category}   (from {source})")
    print(f"  task      {args.task}    era-mode {args.era_mode}")
    print(f"  out       {base}")
    print()
    if key and not os.environ.get(key) and not args.dry_run:
        # .env is loaded by numagent.config inside each stage, so an unset
        # variable here is not fatal — say so rather than refusing to start.
        print(f"  note: {key} is not in this shell; each stage still reads .env\n")

    base.mkdir(parents=True, exist_ok=True)
    manifest = {"pr": args.pr, "repo": full, "number": number, "slug": slug,
                "model": args.model, "provider": provider, "task": args.task,
                "category": category, "category_source": source,
                "era_mode": args.era_mode,
                "build_mode": args.build_mode,
                "package": bool(args.package),
                "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "stages": []}

    def save():
        (base / "manifest.json").write_text(json.dumps(manifest, indent=1),
                                            encoding="utf-8")

    # ---- 1. split patches ------------------------------------------------- #
    # `split-patches` reads a candidates JSON, and the only field it needs is the
    # reference: one hand-written row makes a single PR look like a mined batch.
    cand = base / "candidate.json"
    cand.write_text(json.dumps([{"reference": args.pr}], indent=1), encoding="utf-8")
    stage = Stage("1-split", [PY, "-m", "numbugminer", "split-patches", str(cand),
                              "--out-dir", str(patches)], patches / "index.json")
    manifest["stages"].append(run(stage, force=args.force, dry=args.dry_run,
                                  log_dir=logs))
    save()
    if manifest["stages"][-1]["status"] == "failed":
        return 1
    if args.stop_after == "split":
        return 0

    # ---- 2. mine + strategy ----------------------------------------------- #
    ctx_file = mined / f"{prefix}_{number}.context.json"
    suffix = ".strategies.json" if args.task == "reproduction" else ".brtg.strategies.json"
    strat_file = mined / f"{prefix}_candidates{suffix}"
    argv = [PY, "-m", "NumericSpecificMiner", "mine", args.pr,
            "--category", category,
            "--task", "hbec" if args.task == "reproduction" else "brtg_no_diff",
            "--strategy",
            "--strategy-tasks", args.task,
            "--patches-dir", str(patches),
            "--out-dir", str(mined),
            "--provider", provider, "--model", args.model,
            "--strategy-model", args.model]
    manifest["stages"].append(run(Stage("2-mine", argv, ctx_file),
                                  force=args.force, dry=args.dry_run, log_dir=logs))
    save()
    if manifest["stages"][-1]["status"] == "failed":
        return 1
    if not args.dry_run and not strat_file.exists():
        print(f"  [FAIL] 2-mine produced no strategy at {strat_file}")
        manifest["stages"][-1].update(status="failed",
                                      note="context written, strategy missing")
        save()
        return 1
    if args.stop_after == "mine":
        return 0

    # ---- 3. reproduce ----------------------------------------------------- #
    result = base / f"{slug}.result.json"
    argv = [PY, "-m", "NumericPEVR", "run", str(patches / "index.json"),
            "--pr", str(number), "--repo", repo,
            "--task", args.task, "--grading", args.grading,
            "--context", str(ctx_file), "--strategies", str(strat_file),
            "--patches-dir", str(patches),
            "--provider", provider, "--model", args.model,
            "--max-iterations", str(args.max_iterations),
            "--evidence-budget", str(args.evidence_budget),
            "--cmd-timeout", str(args.cmd_timeout),
            "--era-mode", args.era_mode,
            "--build-mode", args.build_mode,
            *(("--package", "--image-prefix", args.image_prefix,
               "--image-tag", args.image_tag) if args.package else ()),
            *(("--artifacts-dir", args.artifacts_dir) if args.artifacts_dir else ()),
            "--out", str(result), "--log", str(logs / "3-reproduce.trace.txt")]
    manifest["stages"].append(run(Stage("3-reproduce", argv, result),
                                  force=args.force, dry=args.dry_run, log_dir=logs))
    save()

    # ---- what happened ---------------------------------------------------- #
    if args.dry_run:
        print("\n  dry run: nothing executed.")
        return 0
    if not result.exists():
        print("\n  no result file — see the stage log above.")
        return 1
    d = json.loads(result.read_text(encoding="utf-8"))
    d = d[0] if isinstance(d, list) else d
    rep = d.get("report") or {}
    pv = rep.get("numeric_pevr") or {}
    tok = (pv.get("tokens") or {}).get("total") or {}
    tm = d.get("timing") or rep.get("timing") or {}
    manifest["verdict"] = {
        "final": pv.get("final"), "status": d.get("status"),
        "transition": d.get("transition"), "iterations": pv.get("iterations"),
        "evidence_calls": pv.get("evidence_calls"),
        "tokens": tok.get("input_tokens", 0) + tok.get("output_tokens", 0),
        "cost_usd": tok.get("cost_usd"), "wall_min": tm.get("wall_min"),
    }

    # ---- what the whole pipeline cost ------------------------------------- #
    # Mining's usage is recorded in the context it produced, not in a result
    # file, so the two stages have to be added up here. The strategy generator's
    # usage is NOT a third term: the strategies file records the CONTEXT's usage
    # under `miner_usage`, the same three calls, so counting both double-counts.
    mine_use = {}
    if ctx_file.exists():
        try:
            mine_use = (json.loads(ctx_file.read_text(encoding="utf-8")).get("usage")
                        or {})
        except Exception:                                # noqa: BLE001
            mine_use = {}
    m_in = int(mine_use.get("input_tokens") or 0)
    m_out = int(mine_use.get("output_tokens") or 0)
    m_cost = float(mine_use.get("cost_usd") or 0.0)
    m_calls = int(mine_use.get("llm_calls") or 0)
    r_in = int(tok.get("input_tokens") or 0)
    r_out = int(tok.get("output_tokens") or 0)
    r_cost = float(tok.get("cost_usd") or 0.0)
    r_calls = int(tok.get("calls") or 0)

    per_stage = {s["stage"]: s.get("seconds") or 0.0 for s in manifest["stages"]}
    # Wall clock for the pipeline is the sum of its stages, which is the real
    # elapsed time because they run in sequence. A SKIPPED stage contributes 0,
    # so a resumed run reports what THIS invocation spent, not what the instance
    # cost in total — `stages[].status` says which is which.
    total_s = sum(per_stage.values())
    manifest["totals"] = {
        "wall_seconds": round(total_s, 1),
        "wall_minutes": round(total_s / 60, 2),
        "stage_seconds": {k: round(v, 1) for k, v in per_stage.items()},
        "tokens": {
            "mining": {"input": m_in, "output": m_out, "total": m_in + m_out,
                       "cost_usd": round(m_cost, 6), "llm_calls": m_calls},
            "reproduce": {"input": r_in, "output": r_out, "total": r_in + r_out,
                          "cost_usd": round(r_cost, 6), "llm_calls": r_calls},
            "total": {"input": m_in + r_in, "output": m_out + r_out,
                      "total": m_in + m_out + r_in + r_out,
                      "cost_usd": round(m_cost + r_cost, 6),
                      "llm_calls": m_calls + r_calls},
        },
    }
    manifest["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save()

    v, T = manifest["verdict"], manifest["totals"]
    tt = T["tokens"]
    print(f"\n=== {full}#{number} — {args.model} ===")
    print(f"  category    {category} (from {source})")
    print(f"  verdict     {v['final']}   (numagent status: {v['status']})")
    print(f"  transition  {v['transition']}")
    print(f"  iterations  {v['iterations']}   retrievals {v['evidence_calls']}")
    print()
    print(f"  {'stage':12}{'seconds':>10}{'input':>10}{'output':>9}"
          f"{'tokens':>10}{'cost':>10}")
    for name, label in (("1-split", "1-split"), ("2-mine", "2-mine"),
                        ("3-reproduce", "3-reproduce")):
        secs = per_stage.get(name)
        if secs is None:
            continue
        key = {"2-mine": "mining", "3-reproduce": "reproduce"}.get(name)
        c = tt[key] if key else None
        if c:
            print(f"  {label:12}{secs:9.1f}s{c['input']:10,}{c['output']:9,}"
                  f"{c['total']:10,}{'$'+format(c['cost_usd'],'.4f'):>10}")
        else:
            print(f"  {label:12}{secs:9.1f}s{'-':>10}{'-':>9}{'-':>10}{'-':>10}")
    print(f"  {'TOTAL':12}{T['wall_seconds']:9.1f}s{tt['total']['input']:10,}"
          f"{tt['total']['output']:9,}{tt['total']['total']:10,}"
          f"{'$'+format(tt['total']['cost_usd'],'.4f'):>10}")
    print(f"\n  total wall  {T['wall_seconds']:.1f} s  ({T['wall_minutes']:.2f} min)")
    print(f"  total tokens {tt['total']['total']:,}  "
          f"({tt['total']['input']:,} in / {tt['total']['output']:,} out)  "
          f"{tt['total']['llm_calls']} LLM call(s)  ${tt['total']['cost_usd']:.4f}")
    if v["wall_min"]:
        # the agent's own measurement of stage 3, which excludes this script's
        # subprocess overhead — the two should agree to within a second or two
        print(f"  (agent-measured reproduction wall: {v['wall_min']} min)")
    print(f"\n  result    {result}")
    print(f"  manifest  {base / 'manifest.json'}")
    print(f"  trace     {logs / '3-reproduce.trace.txt'}")
    return 0 if v["final"] in ("F2P", "VALID_F2P") else 3


if __name__ == "__main__":
    sys.exit(main())

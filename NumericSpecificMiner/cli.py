"""Command line for NumericSpecificMiner.

    python -m NumericSpecificMiner mine <PR-URL> --category accuracy [--strategy]
    python -m NumericSpecificMiner batch <instances.csv> --out-dir DIR
    python -m NumericSpecificMiner expand <context.json> --result <result.json>
    python -m NumericSpecificMiner show <context.json>

`mine` is the pipeline of the design: crawl, clone, view, three questions,
compact context, and — with `--strategy` — the strategies files the reproducer
reads: one for reproduction and one for BRTG. The category is REQUIRED: this
miner does not classify.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from numbugminer.config import load_config

from . import completeness as comp
from . import escalate as escalate_mod
from . import expand as expand_mod
from . import miner, strategy as strategy_mod
from .context import NumericalContext
from .questions import CATEGORIES
from .views import NO_DIFF, VIEWS

ROOT = Path(__file__).resolve().parent.parent

#: where contexts and strategies land unless --out-dir says otherwise
OUT_DIR = ROOT / "Strategy_numeric"


def _slug(repo: str, pr: int) -> str:
    return f"{repo.replace('/', '__')}_{pr}"


def _categories_from_csv(path) -> dict:
    """(repo, pr) -> category, from the annotation CSV."""
    out = {}
    for row in csv.DictReader(Path(path).open(encoding="utf-8")):
        link = row.get("pr-link") or row.get("pr_link") or ""
        m = re.search(r"/pull/(\d+)", link)
        if not m:
            continue
        label = (row.get("category") or "").strip().lower().replace(" ", "_")
        out[((row.get("repo") or "").strip(), int(m.group(1)))] = label
    return out


def _mine_one(args, reference: str, category: str, cfg) -> dict:
    res = miner.mine_pr(cfg, reference, category=category, task=args.task,
                        provider=args.provider, model=args.model, host=args.host,
                        embed_provider=args.embed_provider,
                        embed_model=args.embed_model,
                        work_dir=args.work_dir, chroma_dir=args.chroma_dir,
                        cleanup_clone=not args.keep_clone,
                        repair_calls=args.repair_calls)
    ctx = res.context
    size = ctx.compression(res.raw_chars)
    print(f"  {ctx.repo}#{ctx.pr_number} [{ctx.category or 'uncategorised'}] "
          f"view={ctx.view} | context {size['context_chars']:,} of "
          f"{size['raw_chars']:,} chars ({size['ratio']:.3f}) | "
          f"{ctx.usage.get('llm_calls', 0)} call(s) "
          f"${ctx.usage.get('cost_usd', 0):.4f} | {res.seconds}s"
          + (f" | {res.note}" if res.note else ""), file=sys.stderr)
    for qid, err in (ctx.errors or {}).items():
        print(f"    ! {qid}: {err}", file=sys.stderr)
    for task, rep in (res.completeness or {}).items():
        gaps = [g["path"].split(".")[-1] for g in rep["required_missing"]]
        print(f"    completeness[{task}] {rep['required_present']}/"
              f"{rep['required_total']}"
              + (f"  still missing: {', '.join(gaps)}" if gaps else "  complete"),
              file=sys.stderr)
    iso = res.audit or {}
    if iso and not iso.get("ok", True):
        print(f"    ! ISOLATION: leaked {len(iso.get('leaked_ngrams', []))} n-gram(s) "
              f"from {iso.get('withheld')}", file=sys.stderr)
    return {"context": ctx, "compression": size, "result": res}


def _write_context(ctx: NumericalContext, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{_slug(ctx.repo, ctx.pr_number)}.context.json"
    path.write_text(ctx.to_json(indent=2), encoding="utf-8")
    return path


def _kinds(args) -> tuple:
    """Which strategies to write. Both by default: one context answers both tasks,
    and the second call is the cheap part of the pipeline."""
    wanted = [k.strip() for k in (args.strategy_tasks or "").split(",") if k.strip()]
    bad = [k for k in wanted if k not in strategy_mod.KINDS]
    if bad:
        raise SystemExit(f"error: unknown --strategy-tasks {', '.join(bad)}; "
                         f"expected {', '.join(strategy_mod.KINDS)}")
    return tuple(wanted) or strategy_mod.KINDS


def _test_patch(args, ctx) -> str:
    """The developer's test diff, if this view is allowed to hold it.

    Under any view that withholds `test_diff` the patch IS the ground truth being
    predicted, so --patches-dir is refused rather than quietly ignored: a silent
    drop would look like the flag worked.
    """
    if not args.patches_dir:
        return ""
    view = VIEWS.get(ctx.view) or NO_DIFF
    if "test_diff" in view.withheld:
        print(f"  ! --patches-dir ignored: the {view.name} view withholds the "
              f"developer's test, which is what the patch contains", file=sys.stderr)
        return ""
    pdir = Path(args.patches_dir)
    idx = pdir / "index.json"
    if not idx.is_file():
        return ""
    for e in json.loads(idx.read_text(encoding="utf-8")):
        if (e.get("pr") or e.get("pr_number")) == ctx.pr_number:
            tp = pdir / (e.get("test_patch") or "")
            return tp.read_text(encoding="utf-8") if tp.exists() else ""
    return ""


def _strategies_file(args, ctx, kind: str, out_dir: Path) -> Path:
    """--strategies-out names the reproduction file (or a directory); the BRTG
    file sits beside it under its own name."""
    if not args.strategies_out:
        return strategy_mod.strategies_path(out_dir, ctx.repo, kind)
    out = Path(args.strategies_out)
    if out.is_dir():
        return strategy_mod.strategies_path(out, ctx.repo, kind)
    if kind == strategy_mod.REPRODUCTION:
        return out
    stem = out.name[:-len(".strategies.json")] if out.name.endswith(".strategies.json") \
        else out.stem
    return out.with_name(f"{stem}.{kind}.strategies.json")


def cmd_mine(args) -> int:
    cfg = load_config()
    category = (args.category or "").strip().lower()
    if args.categories and not category:
        m = re.search(r"github\.com/([^/]+/[^/]+)/pull/(\d+)", args.reference)
        if m:
            category = _categories_from_csv(args.categories).get(
                (m.group(1), int(m.group(2))), "")
    if not category:
        print("error: --category is required (accuracy | special_value | "
              "correctness | convergence), or --categories CSV must cover this PR.\n"
              "       This miner does not classify; the annotation decides.",
              file=sys.stderr)
        return 2
    if category not in CATEGORIES:
        print(f"error: unknown category {category!r}; expected one of "
              f"{', '.join(CATEGORIES)}", file=sys.stderr)
        return 2

    one = _mine_one(args, args.reference, category, cfg)
    ctx = one["context"]
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    path = _write_context(ctx, out_dir)
    print(f"context -> {path}", file=sys.stderr)

    if args.strategy:
        patch = _test_patch(args, ctx)
        for kind in _kinds(args):
            rec = strategy_mod.generate(ctx, provider=args.provider,
                                        model=args.strategy_model or args.model,
                                        host=args.host, test_patch=patch, kind=kind)
            sfile = _strategies_file(args, ctx, kind, out_dir)
            strategy_mod.write([rec], sfile, provider=args.provider,
                               model=args.strategy_model or args.model,
                               source=str(path))
            plan = rec.get("strategy") or {}
            print(f"strategy[{kind}] -> {sfile}\n    "
                  f"{plan.get('summary') or rec.get('error')}", file=sys.stderr)
            if kind == strategy_mod.BRTG and plan.get("oracle"):
                o = plan["oracle"]
                print(f"    oracle: {o.get('kind')}"
                      + (f" {o.get('tolerance')}" if o.get("tolerance") else "")
                      + (f" | ref: {o.get('reference_source')}"
                         if o.get("reference_source") else ""), file=sys.stderr)
    if args.json:
        print(ctx.to_json(indent=2))
    return 0


def cmd_batch(args) -> int:
    """Mine a list of confirmed instances: `repo,pr-link,category` CSV."""
    cfg = load_config()
    rows = list(csv.DictReader(Path(args.instances).open(encoding="utf-8")))
    out_dir = Path(args.out_dir) if args.out_dir else OUT_DIR
    records, done, failed = [], 0, []
    for row in rows[: args.limit]:
        link = row.get("pr-link") or row.get("pr_link") or ""
        category = (row.get("category") or "").strip().lower().replace(" ", "_")
        if not link or category not in CATEGORIES:
            failed.append((link, f"missing/unknown category {category!r}"))
            continue
        try:
            ctx = _mine_one(args, link, category, cfg)["context"]
        except Exception as e:                      # noqa: BLE001 — one instance
            failed.append((link, f"{type(e).__name__}: {e}"))
            continue
        _write_context(ctx, out_dir)
        done += 1
        if args.strategy:
            for kind in _kinds(args):
                records.append(strategy_mod.generate(
                    ctx, provider=args.provider,
                    model=args.strategy_model or args.model, host=args.host,
                    kind=kind))
    if records:
        # keyed by (repo, task): the harness reads one PR per file, so the two
        # tasks must not land in the same one
        grouped: dict = {}
        for r in records:
            grouped.setdefault((r["repo"], r.get("task") or strategy_mod.REPRODUCTION),
                               []).append(r)
        for (repo, kind), recs in grouped.items():
            strategy_mod.write(recs, strategy_mod.strategies_path(out_dir, repo, kind),
                               provider=args.provider,
                               model=args.strategy_model or args.model,
                               source=str(args.instances))
    print(f"\nmined {done} of {len(rows[:args.limit])} | failed {len(failed)}",
          file=sys.stderr)
    for link, why in failed[:10]:
        print(f"  ! {link}: {why}", file=sys.stderr)
    return 0 if done else 1


def cmd_expand(args) -> int:
    ctx = NumericalContext.from_dict(
        json.loads(Path(args.context).read_text(encoding="utf-8")))
    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    result = result[0] if isinstance(result, list) else result
    req = expand_mod.diagnose(result)
    gaps = comp.assess(ctx, comp.BRTG)["required_missing"]
    if req is None and not gaps:
        print("no expansion: the run reproduced the bug, or more evidence cannot "
              "fix what failed", file=sys.stderr)
        return 0
    if req:
        print(f"diagnosis: {req.kind} -> re-asking '{req.question_id}'", file=sys.stderr)
    budget = escalate_mod.Budget(max_calls=args.max_calls, max_rung=args.max_rung)
    ctx, budget = escalate_mod.climb(
        ctx, result=result, budget=budget, provider=args.provider,
        model=args.model, host=args.host, embed_provider=args.embed_provider,
        embed_model=args.embed_model, chroma_dir=args.chroma_dir)
    for entry in budget.log:
        print(f"  rung {entry['rung']:9s} {entry['input_tokens']:>6,} in / "
              f"{entry['output_tokens']:>5,} out  ${entry['cost_usd']:.4f}  "
              f"{entry['note'][:60]}", file=sys.stderr)
    after = comp.assess(ctx, comp.BRTG)
    print(f"  completeness now {after['required_present']}/{after['required_total']}",
          file=sys.stderr)
    out = Path(args.out) if args.out else Path(args.context)
    out.write_text(ctx.to_json(indent=2), encoding="utf-8")
    print(f"expanded context -> {out}", file=sys.stderr)
    return 0


def cmd_show(args) -> int:
    ctx = NumericalContext.from_dict(
        json.loads(Path(args.context).read_text(encoding="utf-8")))
    print(ctx.to_prompt())
    if args.provenance:
        print("\n--- provenance ---")
        for f in ctx.provenance:
            print(f"  {f['field']:44s} {f['source']:12s} {f['evidence_location']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="NumericSpecificMiner", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--provider", default="openai")
        sp.add_argument("--model", default="")
        sp.add_argument("--host", default="")
        sp.add_argument("--embed-provider", default="ollama")
        sp.add_argument("--embed-model", default="")
        sp.add_argument("--chroma-dir", default=None)
        sp.add_argument("--out-dir", default=None)

    m = sub.add_parser("mine", help="one confirmed numerical bug -> context [+ strategy]")
    m.add_argument("reference", help="PR URL")
    m.add_argument("--category", default="", choices=("",) + CATEGORIES,
                   help="the ANNOTATED category (required unless --categories covers it)")
    m.add_argument("--categories", default=None, metavar="CSV",
                   help="annotation CSV (repo,pr-link,category) to read the category from")
    m.add_argument("--task", default="no_diff", choices=tuple(VIEWS),
                   help="the evidence view to mine. no_diff (default) indexes no "
                        "diff at all, for any question; brtg_* withhold the "
                        "developer's test but let the environment question read "
                        "the fix; hbec sees everything")
    m.add_argument("--strategy", action="store_true",
                   help="also write the *.strategies.json the reproducer consumes")
    m.add_argument("--strategy-tasks", default="",
                   help="comma-separated: reproduction, brtg (default: both)")
    m.add_argument("--strategies-out", default=None)
    m.add_argument("--strategy-model", default=None)
    m.add_argument("--patches-dir", default=None,
                   help="split-patches dir; its test diff is given to the strategy "
                        "writer (HBEC only — never under brtg_no_diff)")
    m.add_argument("--work-dir", default=None)
    m.add_argument("--keep-clone", action="store_true")
    m.add_argument("--repair-calls", type=int, default=1,
                   help="LLM calls the miner may spend closing REQUIRED evidence "
                        "gaps before writing the context (0 disables the repair)")
    m.add_argument("--json", action="store_true")
    common(m)
    m.set_defaults(func=cmd_mine)

    b = sub.add_parser("batch", help="mine a CSV of confirmed instances")
    b.add_argument("instances", help="CSV with repo,pr-link,category")
    b.add_argument("--limit", type=int, default=None)
    b.add_argument("--task", default="no_diff", choices=tuple(VIEWS))
    b.add_argument("--strategy", action="store_true")
    b.add_argument("--strategy-tasks", default="",
                   help="comma-separated: reproduction, brtg (default: both)")
    b.add_argument("--strategy-model", default=None)
    b.add_argument("--work-dir", default=None)
    b.add_argument("--keep-clone", action="store_true")
    b.add_argument("--repair-calls", type=int, default=1)
    common(b)
    b.set_defaults(func=cmd_batch)

    e = sub.add_parser("expand", help="adaptive evidence expansion after a failure")
    e.add_argument("context", help="a *.context.json written by mine")
    e.add_argument("--result", required=True, help="the numagent result JSON")
    e.add_argument("--out", default=None)
    e.add_argument("--max-calls", type=int, default=2,
                   help="LLM calls this escalation may spend")
    e.add_argument("--max-rung", default="widened",
                   choices=escalate_mod.RUNGS,
                   help="how far up the ladder it may climb")
    common(e)
    e.set_defaults(func=cmd_expand)

    s = sub.add_parser("show", help="print a context as the Strategy Generator sees it")
    s.add_argument("context")
    s.add_argument("--provenance", action="store_true")
    s.set_defaults(func=cmd_show)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

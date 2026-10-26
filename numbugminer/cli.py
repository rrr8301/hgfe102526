from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .pipeline import Report, analyze
from .swebench_eval import DEFAULT_FIELDS, TEXT_FIELDS


def _print_human(report: Report) -> None:
    print(f"reference : {report.reference}")
    print(f"source    : {report.source}")
    if report.pr:
        p = report.pr
        print(f"PR        : {p['title']}")
        print(f"state     : {'merged' if p['merged'] else p['state']} "
              f"| files: {p['n_files']} | commits: {p['n_commits']}")
        buggy = (p.get("buggy_commit") or "")[:12] or "?"
        fixed = (p.get("fixed_commit") or "")[:12] or "?"
        print(f"commits   : buggy {buggy} -> fixed {fixed}")
        if p.get("commit_pair_note"):
            print(f"            ({p['commit_pair_note']})")
        if p["linked_issues"]:
            issues = ", ".join(f"#{li['number']}({li['relation']})" for li in p["linked_issues"])
            print(f"linked    : {issues}")
        if p["ci_logs"]:
            logs = ", ".join(f"{c['name']}:{c['conclusion']}" for c in p["ci_logs"])
            print(f"ci logs   : {logs}")
        if p["warnings"]:
            print(f"warnings  : {len(p['warnings'])} (use --json to see them)")
    print(f"signals   : {', '.join(report.signals) or '(none)'}")
    print("retrieved :")
    for r in report.retrieved:
        mark = "N" if r["label"] == "numerical_bug" else "-"
        print(f"    [{mark}] {r['id']:<28} {r['bug_type']:<14} sim={r['score']:.3f}")

    if report.note:
        print(f"note      : {report.note}")
    if report.verdict:
        v = report.verdict
        verdict = "NUMERICAL BUG" if v["is_numerical_bug"] else "not a numerical bug"
        print()
        print(f"VERDICT   : {verdict}  (type={v['bug_type']}, confidence={v['confidence']:.2f})")
        if report.pr is not None:
            writes = v.get("writes_numerical_test", False)
            tf = report.pr.get("test_files") or []
            note = f"  ({', '.join(tf)})" if writes and tf else ""
            print(f"num. test : {'YES' if writes else 'no'}{note}")
        print(f"why       : {v['explanation']}")
        if v["evidence"]:
            print("evidence  :")
            for e in v["evidence"]:
                print(f"    - {e['line']}")
                print(f"      -> {e['reason']}")
    if report.usage:
        u = report.usage
        print(f"cost      : ${u['cost_usd']:.4f} ({u['model']} | "
              f"in {u['input_tokens']}, out {u['output_tokens']}, "
              f"cache_read {u['cache_read_tokens']})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="numbugminer",
        description="RAG-based numerical-bug miner for GitHub PRs and CI logs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="classify a PR (or CI log) as a numerical bug or not")
    a.add_argument("reference", nargs="?", default="",
                   help="PR URL, owner/repo#N, bare number (+ --repo --pr), or a run/job ref")
    a.add_argument("--repo", default=None, help="OWNER/REPO for a bare number")
    a.add_argument("--pr", action="store_true", help="treat a bare number as a PR (not a run)")
    a.add_argument("--job-id", default=None, help="(log path) specific Actions job id")
    a.add_argument("--log-file", default=None, help="classify a local log file instead of fetching")
    a.add_argument("--with-logs", action="store_true",
                   help="(PR path) include CI logs in the RAG context (default: excluded — "
                        "logs are noisy and expensive to fetch)")
    a.add_argument("--no-commit-tests", action="store_true",
                   help="(PR path) skip per-commit test-writing detection (saves API calls; "
                        "the writes_numerical_test question then relies on PR-level evidence)")
    a.add_argument("--allow-unmerged", action="store_true",
                   help="(PR path) analyze a PR even if it wasn't merged (default: merged "
                        "PRs only — only they have a landed fix / settled commit pair)")
    a.add_argument("--no-llm", action="store_true", help="retrieval only; skip the Claude verdict")
    a.add_argument("--keyword", action="store_true",
                   help="RAG-free keyword-filter verdict (no corpus, embeddings, or model call)")
    a.add_argument("--corpus", default=None,
                   help="seed corpus to retrieve from (default: the Claude set)")
    a.add_argument("--json", action="store_true", help="emit the full report as JSON")

    c = sub.add_parser("crawl", help="list a repository's closed PRs (discovery input for analyze)")
    c.add_argument("repo", help="OWNER/REPO (e.g. scipy/scipy)")
    c.add_argument("--merged", action="store_true", help="only merged PRs (fix actually landed)")
    c.add_argument("--keyword-filter", action="store_true",
                   help="only PRs whose title/body trip the numerical-bug keyword filter")
    c.add_argument("--test-commits", action="store_true",
                   help="only PRs with a commit that writes test cases (adds lines to a test "
                        "file); implies --keyword-filter, so: numerical PRs that ship a test")
    c.add_argument("--commit-cap", type=int, default=50,
                   help="(--test-commits) max commits to inspect per PR (default: 50)")
    c.add_argument("--strict", action="store_true",
                   help="require the numerical keyword score to clear the threshold "
                        "(drops lone weak hits like `exception`); default keeps any hit")
    c.add_argument("--allow-test-only", action="store_true",
                   help="keep PRs that add tests without changing any source file; by "
                        "default a candidate must modify BOTH (the test exposes the bug "
                        "on the buggy revision, the source change makes it pass)")
    c.add_argument("--no-bug-intent", action="store_true",
                   help="keep numerically-relevant PRs even without bug-fixing intent "
                        "(no bug label, no fix/bug wording in the title); by default a "
                        "candidate must show both")
    c.add_argument("--include-bumps", action="store_true",
                   help="keep automated dependency-bump PRs (dependabot/renovate, "
                        "`chore(deps): bump ...`); they are dropped by default")
    c.add_argument("--limit", type=int, default=None,
                   help="max PRs to return (default: 20, or unlimited with --since)")
    c.add_argument("--since", default=None,
                   help="only PRs created on/after this date (YYYY-MM-DD)")
    c.add_argument("--until", default=None, help="only PRs created on/before this date")
    c.add_argument("--scan-cap", type=int, default=None,
                   help="max closed PRs to page through (default: 1000, or UNLIMITED with "
                        "--since). 0 = unlimited")
    c.add_argument("--json", action="store_true", help="emit the PR list as JSON")

    s = sub.add_parser("swebench",
                       help="run the keyword filter over SWE-bench data (RAG-free prescreen)")
    s.add_argument("source", nargs="?", default="princeton-nlp/SWE-bench_Lite",
                   help="a .jsonl/.parquet path, or a HF dataset id "
                        "(default: princeton-nlp/SWE-bench_Lite)")
    s.add_argument("--config", default="default", help="(HF source) dataset config name")
    s.add_argument("--split", default="test", help="(HF source) split to load (default: test)")
    s.add_argument("--limit", type=int, default=None, help="max instances to evaluate")
    s.add_argument("--offset", type=int, default=0, help="skip this many instances first")
    s.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                   help=f"comma-separated instance fields to scan "
                        f"(default: {','.join(DEFAULT_FIELDS)}; available: {','.join(TEXT_FIELDS)})")
    s.add_argument("--threshold", type=float, default=None,
                   help="weighted-score firing threshold (default: the filter's own)")
    s.add_argument("--flagged-only", action="store_true",
                   help="only list instances the filter flagged as numerical bugs")
    s.add_argument("--out", default=None,
                   help="write per-instance verdicts to this JSONL file")
    s.add_argument("--json", action="store_true",
                   help="emit {summary, results} as JSON instead of a table")

    # SWT-bench shares the SWE-bench instance schema, so it reuses swebench_eval;
    # this is a named entry point with the SWT-bench default source.
    w = sub.add_parser("swtbench",
                       help="run the keyword filter over SWT-bench data (SWE-bench schema)")
    w.add_argument("source", nargs="?", default="eth-sri/SWT-bench_Lite_bm25_27k_zsb",
                   help="a .jsonl/.parquet path, or a HF dataset id "
                        "(default: eth-sri/SWT-bench_Lite_bm25_27k_zsb)")
    w.add_argument("--config", default="default", help="(HF source) dataset config name")
    w.add_argument("--split", default="test", help="(HF source) split to load (default: test)")
    w.add_argument("--limit", type=int, default=None, help="max instances to evaluate")
    w.add_argument("--offset", type=int, default=0, help="skip this many instances first")
    w.add_argument("--fields", default=",".join(DEFAULT_FIELDS),
                   help=f"comma-separated instance fields to scan "
                        f"(default: {','.join(DEFAULT_FIELDS)}; available: {','.join(TEXT_FIELDS)})")
    w.add_argument("--threshold", type=float, default=None,
                   help="weighted-score firing threshold (default: the filter's own)")
    w.add_argument("--flagged-only", action="store_true",
                   help="only list instances the filter flagged as numerical bugs")
    w.add_argument("--out", default=None, help="write per-instance verdicts to this JSONL file")
    w.add_argument("--json", action="store_true",
                   help="emit {summary, results} as JSON instead of a table")

    d = sub.add_parser("defects4j",
                       help="run the keyword filter over Defects4J bugs (RAG-free prescreen)")
    d.add_argument("source", nargs="?", default="github",
                   help="a .jsonl path, a local Defects4J checkout dir, or 'github' "
                        "(default: fetch from rjust/defects4j)")
    d.add_argument("--projects", default=None,
                   help="comma-separated projects to load (default: all 17; e.g. Math,Lang)")
    d.add_argument("--limit", type=int, default=None, help="max bugs to evaluate")
    d.add_argument("--fields", default="trigger_tests,modified_classes",
                   help="comma-separated bug fields to scan (default: trigger_tests,"
                        "modified_classes; available: trigger_tests,modified_classes,report_id)")
    d.add_argument("--threshold", type=float, default=None,
                   help="weighted-score firing threshold (default: the filter's own)")
    d.add_argument("--flagged-only", action="store_true",
                   help="only list bugs the filter flagged as numerical")
    d.add_argument("--out", default=None, help="write per-bug verdicts to this JSONL file")
    d.add_argument("--json", action="store_true",
                   help="emit {summary, results} as JSON instead of a table")

    m = sub.add_parser("mine",
                       help="repo -> candidate numerical-bug PRs + RAG reproduction info "
                            "(keyword prescreen -> clone -> ChromaDB/Ollama RAG -> structured Qs)")
    m.add_argument("repo", help="OWNER/REPO (e.g. scipy/scipy), or a single PR (owner/repo#N)")
    m.add_argument("--limit", type=int, default=None,
                   help="max candidate PRs to mine (default: 10, or unlimited with --since)")
    m.add_argument("--since", default=None,
                   help="only PRs created on/after this date (YYYY-MM-DD). Stops the scan "
                        "at the boundary, so a dated corpus costs one page per window")
    m.add_argument("--until", default=None, help="only PRs created on/before this date")
    m.add_argument("--scan-cap", type=int, default=None,
                   help="max closed PRs to page through (default: 1000, or UNLIMITED with "
                        "--since, so the date bounds the scan rather than the cap). 0 = unlimited")
    m.add_argument("--allow-test-only", action="store_true",
                   help="(--require-tests) keep PRs that add tests without changing source; "
                        "by default a candidate must modify BOTH")
    m.add_argument("--require-tests", action="store_true",
                   help="apply the test-modification filter before RAG (the pre-RAG "
                        "pipeline's last stage); costs one files call + a commit drill per PR")
    m.add_argument("--provider", choices=["ollama", "anthropic", "openai", "deepseek"], default="ollama",
                   help="RAG generation provider (default: ollama, locally hosted)")
    m.add_argument("--model", default=None, help="generation model (default: provider default)")
    m.add_argument("--embed-provider", default=None,
                   help="embedding provider (default: same as --provider)")
    m.add_argument("--embed-model", default=None, help="embedding model (default: provider default)")
    m.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    m.add_argument("--work-dir", default=None,
                   help="clone dir for a single-PR mine (default: workspace/{repo}_{PR})")
    m.add_argument("--only", default=None, help="comma-separated question ids to run")
    m.add_argument("--strategies-out", default=None, metavar="PATH",
                   help="also write a *.strategies.json the reproducer consumes "
                        "directly, generated from the answers just mined — no "
                        "separate `repro-strategy` pass (default: beside the "
                        "candidates file)")
    m.add_argument("--strategies-patches-dir", default=None, metavar="DIR",
                   help="split-patches dir whose test diffs feed the strategy "
                        "writer, so it can quote concrete input/expected values "
                        "from the patch's own assertions")
    m.add_argument("--strategy-model", default=None,
                   help="model for the strategy pass (default: the mining model)")
    m.add_argument("--categories", default=None, metavar="CSV",
                   help="curated bug categories (repo,pr-link,category) written "
                        "into each strategy's bug_type. With --no-q1 the miner "
                        "asks for no category, so this is where one comes from; "
                        "the index built by make_index_with_bugtype.py carries "
                        "the same labels and still wins at run time")
    m.add_argument("--no-web", action="store_true",
                   help="do not fetch the Web pages the PR, issues and README reference "
                        "(they are fetched and indexed by default, once each, no crawling)")
    m.add_argument("--max-web-docs", type=int, default=8,
                   help="cap on referenced pages fetched per candidate (default: 8)")
    m.add_argument("--keep-clones", action="store_true",
                   help="keep the cloned repo folders (default: remove each after its "
                        "answers are extracted, to bound workspace/ disk)")
    m.add_argument("--out", default=None,
                   help="output JSON (default: workspace/new-mine/<repo>_candidates.json)")
    m.add_argument("--json", action="store_true", help="also print the result JSON to stdout")
    m.add_argument("--log", default=None,
                   help="also write the console trace to this text file (default: the "
                        "--out path with a .txt suffix)")

    pb = sub.add_parser("probe",
                        help="clone the repo at the buggy commit and answer Q1-Q7 about the PR "
                             "via document-RAG (numerical-bug + reproducibility)")
    pb.add_argument("reference", help="PR URL, owner/repo#N, or bare number (+ --repo)")
    pb.add_argument("--repo", default=None, help="OWNER/REPO for a bare number")
    pb.add_argument("--provider", choices=["anthropic", "openai", "deepseek", "ollama"],
                    default="anthropic", help="RAG generation provider (default: anthropic)")
    pb.add_argument("--model", default=None, help="chat model (default: provider's default)")
    pb.add_argument("--embed-model", default=None,
                    help="embedding model (default: provider-matched; TF-IDF offline)")
    pb.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    pb.add_argument("--work-dir", default=None,
                    help="where to clone (default: workspace/{repo}_{PR})")
    pb.add_argument("--no-clone", action="store_true",
                    help="skip cloning; probe PR documents only (no README/CI/lockfiles)")
    pb.add_argument("--allow-unmerged", action="store_true",
                    help="probe even if the PR wasn't merged (no settled buggy commit)")
    pb.add_argument("--apply-test-patch", action="store_true",
                    help="apply ONLY the PR's test-file hunks onto the buggy checkout, so the "
                         "new test fails against buggy code (fail→pass setup)")
    pb.add_argument("--only", default=None,
                    help="comma-separated question ids to run (e.g. Q1,Q2); default: all")
    pb.add_argument("--out", default=None,
                    help="where to save the report JSON (default: <workdir>/probe_report.json)")
    pb.add_argument("--json", action="store_true", help="also emit the report as JSON to stdout")

    pc = sub.add_parser("probe-compare",
                        help="diff two or more saved probe reports into a side-by-side table")
    pc.add_argument("reports", nargs="+", help="probe report JSON files (2+)")
    pc.add_argument("--out", default=None, help="save the comparison as JSON to this path")
    pc.add_argument("--json", action="store_true", help="emit the comparison as JSON to stdout")

    sp = sub.add_parser("split-patches",
                        help="split each mined PR's diff into source (fix) + test patches, "
                             "saved under workspace/patches/")
    sp.add_argument("input", help="a mine candidates JSON")
    sp.add_argument("--numerical-only", action="store_true",
                    help=argparse.SUPPRESS)   # accepted and ignored: the miner no
                    # longer classifies, so there is no flag to filter on
    sp.add_argument("--out-dir", default=None,
                    help="output dir (default: workspace/patches)")

    rs = sub.add_parser("repro-strategy",
                        help="turn mined candidate record(s) into an agent-executable "
                             "reproduction strategy via an LLM")
    rs.add_argument("input", help="a mine candidates JSON (or a single-record JSON)")
    rs.add_argument("--index", type=int, default=0, help="candidate index to use (default: 0)")
    rs.add_argument("--pr", type=int, default=None, help="select candidate by PR number")
    rs.add_argument("--all", action="store_true", help="generate a strategy for every candidate")
    rs.add_argument("--numerical-only", action="store_true",
                    help=argparse.SUPPRESS)   # accepted and ignored, as above
    rs.add_argument("--provider", choices=["anthropic", "openai", "deepseek", "ollama"], default="anthropic")
    rs.add_argument("--model", default=None)
    rs.add_argument("--host", default="http://localhost:11434")
    rs.add_argument("--patches-dir", default=None,
                    help="dir with split test patches + index.json (e.g. workspace/patches); "
                         "when set, the reproducing test diff is fed to the model so it can "
                         "record concrete input/expected-output examples")
    rs.add_argument("--from-pr-readme", action="store_true",
                    help="ABLATION (no miner): write the strategy from the pull request "
                         "(title, body, diff) and the repository's README alone, ignoring "
                         "the mined answers in the input file — which is then read only for "
                         "the repo, PR number and commit pair. Each strategy is stamped "
                         "evidence=pr+readme. Run numagent WITHOUT --candidates to keep the "
                         "mined environment out of the arm too")
    rs.add_argument("--out", default=None, help="output JSON (default: <input>.strategies.json)")
    rs.add_argument("--json", action="store_true", help="print the strategy JSON to stdout")
    rs.add_argument("--only-failed", action="store_true",
                    help="regenerate only the strategies whose previous run failed to "
                         "parse, merging them back into the existing --out file")

    tx = sub.add_parser("taxonomy",
                        help="classify bugs into the 12-leaf numerical-bug taxonomy (LLM)")
    tx.add_argument("input", help="JSONL of per-bug rows (an eval results file or an evidence file)")
    tx.add_argument("--text-fields",
                    default="problem_statement,trigger_tests,modified_classes,evidence,text",
                    help="comma-separated fields concatenated as the evidence")
    tx.add_argument("--id-field", default="id",
                    help="id field (falls back to instance_id/key)")
    tx.add_argument("--flagged-only", action="store_true",
                    help="only classify rows with is_numerical_bug=true")
    tx.add_argument("--provider", choices=["anthropic", "openai", "deepseek", "ollama"], default="anthropic")
    tx.add_argument("--model", default=None)
    tx.add_argument("--host", default="http://localhost:11434")
    tx.add_argument("--limit", type=int, default=None)
    tx.add_argument("--out", default=None, help="output JSON (default: <input>.taxonomy.json)")
    tx.add_argument("--json", action="store_true", help="emit the classifications as JSON")

    g = sub.add_parser("seed-gen", help="meta-prompt an LLM to build/grow a per-provider seed corpus")
    g.add_argument("--provider", choices=["anthropic", "openai", "deepseek"], default="anthropic",
                   help="generation provider; picks the model + output file")
    g.add_argument("--per-type", type=int, default=3, help="examples per taxonomy slot")
    g.add_argument("--model", default=None, help="override the provider's default model")
    g.add_argument("--out", default=None,
                   help="output path (default: data/seed_corpus.<provider>.jsonl)")
    g.add_argument("--replace", action="store_true",
                   help="rebuild from scratch instead of merging the existing set")
    g.add_argument("--with-anchors", action="store_true",
                   help="also include the neutral hand-anchor base (default: provider-generated only)")
    return parser


def _run_seed_gen(args) -> int:
    from pathlib import Path

    from .config import HAND_CORPUS_PATH, seed_corpus_for
    from .seed_gen import generate_seed_corpus

    cfg = load_config()
    out = Path(args.out) if args.out else seed_corpus_for(args.provider)

    if args.provider == "openai":
        import openai

        client = openai.OpenAI()
        model = args.model or cfg.openai_model
    else:
        import anthropic

        client = anthropic.Anthropic()
        model = args.model or cfg.model

    try:
        stats = generate_seed_corpus(
            client, model, out,
            provider=args.provider,
            per_type=args.per_type,
            merge=not args.replace,
            base_path=HAND_CORPUS_PATH if args.with_anchors else None,
            progress=lambda m: print(f"  {m}", file=sys.stderr),
        )
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"[{args.provider}:{model}] generated {stats.generated} | kept {stats.kept} new "
          f"(dropped {stats.duplicates} dupes) | base/existing {stats.existing} "
          f"| total {stats.total} -> {stats.out_path}")
    return 0


def _run_swebench(args) -> int:
    from .keyword_filter import DEFAULT_THRESHOLD
    from .swebench_eval import evaluate, load_instances, summarize

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    bad = [f for f in fields if f not in TEXT_FIELDS]
    if bad:
        print(f"error: unknown field(s) {bad}; available: {', '.join(TEXT_FIELDS)}",
              file=sys.stderr)
        return 1
    threshold = args.threshold if args.threshold is not None else DEFAULT_THRESHOLD

    try:
        instances = load_instances(args.source, config=args.config, split=args.split,
                                   limit=args.limit, offset=args.offset)
    except Exception as e:  # noqa: BLE001 — surface a clean message, not a traceback
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not instances:
        print("error: loaded 0 instances", file=sys.stderr)
        return 1

    results = evaluate(instances, fields=fields, threshold=threshold)
    summary = summarize(results)

    if args.out:
        from pathlib import Path

        out = Path(args.out)
        with out.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r.to_dict()) + "\n")
        print(f"wrote {len(results)} verdicts -> {out}", file=sys.stderr)

    shown = [r for r in results if r.is_numerical_bug] if args.flagged_only else results

    if args.json:
        print(json.dumps({"summary": summary, "results": [r.to_dict() for r in shown]}, indent=2))
        return 0

    for r in shown:
        mark = "N" if r.is_numerical_bug else "-"
        kw = ", ".join(r.keywords)
        print(f"[{mark}] {r.instance_id:<34} {r.bug_type:<12} "
              f"conf={r.confidence:.2f} score={r.score:g}  {kw}")
    print(f"\nscanned fields: {', '.join(fields)}  | threshold: {threshold:g}", file=sys.stderr)
    print(f"flagged {summary['flagged']}/{summary['total']} "
          f"({summary['flag_rate'] * 100:.1f}%) as numerical bugs", file=sys.stderr)
    if summary["bug_types"]:
        bt = ", ".join(f"{k}:{v}" for k, v in summary["bug_types"].items())
        print(f"bug types: {bt}", file=sys.stderr)
    if summary["keyword_freq"]:
        top = ", ".join(f"{k}:{v}" for k, v in list(summary["keyword_freq"].items())[:10])
        print(f"top keywords: {top}", file=sys.stderr)
    return 0


class _Tee:
    """Write to a stream and a file at once.

    A mine prints the funnel as it narrows — every PR scanned, why each was kept
    or dropped, what the four questions answered — and that account exists only
    on the terminal unless it is captured. Keeping it beside the candidates JSON
    means the run can be read back later without paying for it again.
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


def _run_mine_logged(args) -> int:
    """Run the mine, mirroring the console trace to --log when asked."""
    from pathlib import Path as _Path

    log_path = args.log or (str(_Path(args.out).with_suffix(".txt")) if args.out else None)
    if not log_path:
        return _run_mine(args)
    _Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as fh:
        stdout, stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = _Tee(stdout, fh), _Tee(stderr, fh)
        try:
            code = _run_mine(args)
        finally:
            sys.stdout, sys.stderr = stdout, stderr
    print(f"trace   -> {log_path}")
    return code


def _run_mine(args) -> int:
    from pathlib import Path

    from . import mine, pr_source
    from .config import PROJECT_ROOT

    cfg = load_config()
    only = [q.strip() for q in args.only.split(",")] if args.only else None
    # Embeddings default to the generation provider; neither Anthropic nor
    # DeepSeek publishes an embedding API, so that silently drops retrieval to
    # the TF-IDF fallback. Say so rather than letting the answers quietly worsen.
    embedder_provider = args.embed_provider or args.provider
    if embedder_provider in ("anthropic", "deepseek"):
        print(f"note: {embedder_provider} has no embedding API — retrieval will use the "
              f"local TF-IDF fallback. Pass --embed-provider ollama for dense embeddings.",
              file=sys.stderr)
    kw = dict(provider=args.provider, model=args.model, embed_provider=args.embed_provider,
              embed_model=args.embed_model, host=args.host, only=only,
              cleanup_clone=not args.keep_clones,
              fetch_web=not args.no_web, max_web_docs=args.max_web_docs)
    repo_kw = dict(since=args.since, until=args.until, require_tests=args.require_tests,
                   require_source_change=not args.allow_test_only,
                   scan_cap=_scan_cap(args),
                   limit=args.limit if args.limit is not None else (None if args.since else 10))
    try:
        if pr_source.is_pr_ref(args.repo):  # a single PR reference
            rec = mine.mine_pr(cfg, args.repo, work_dir=args.work_dir, **kw)
            result = {"repo": rec.repo, "n_candidates": 1, "provider": args.provider,
                      "usage_totals": mine.usage_totals([rec.to_dict()]),
                      "candidates": [rec.to_dict()]}
            repo_slug = rec.repo.replace("/", "__")
        else:
            result = mine.mine_repo(cfg, args.repo, **repo_kw, **kw,
                                    progress=lambda m: print(f"  {m}", file=sys.stderr))
            repo_slug = args.repo.replace("/", "__")
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1

    # workspace/mine holds the corpus mined by the earlier ten-question pipeline;
    # output from the four-question one lands beside it rather than on top of it.
    out = (Path(args.out) if args.out else
           PROJECT_ROOT / "workspace" / "new-mine" / f"{repo_slug}_candidates.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # Mining ONE pr writes to the repository's candidates file, which usually
    # already holds the rest of that repo's corpus. Truncating it would throw
    # away everything the file cost to mine, so a single-PR run merges: its own
    # record replaces any earlier record for the same PR, the others stay.
    if out.is_file() and result["n_candidates"] == 1:
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            prior = existing.get("candidates", []) if isinstance(existing, dict) else existing
        except Exception:  # noqa: BLE001 — an unreadable file is replaced, not merged
            prior = []
        mined = {c.get("pr_number") for c in result["candidates"]}
        merged = [c for c in prior if c.get("pr_number") not in mined] + result["candidates"]
        result = {**result, "n_candidates": len(merged), "candidates": merged,
                  "usage_totals": mine.usage_totals(merged)}
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    # --strategies-out: go straight from the mined answers to the file the
    # reproducer reads. The candidates file is still written (it is the record of
    # what was asked and retrieved); this just removes the second command, and
    # with --no-q1 there is no verdict for that command to filter on anyway.
    if getattr(args, "strategies_out", None):
        strat_out = Path(args.strategies_out)
        if strat_out.is_dir():
            strat_out = strat_out / f"{repo_slug}_candidates.strategies.json"
        _emit_strategies(result["candidates"], strat_out,
                         provider=args.provider,
                         model=args.strategy_model or args.model,
                         host=args.host,
                         patches_dir=args.strategies_patches_dir,
                         source=str(out), categories=args.categories)

    if args.json:
        print(json.dumps(result, indent=2))
    total_cost = sum((c.get("usage") or {}).get("cost_usd", 0) for c in result["candidates"])
    _print_funnel(result.get("crawl_stats") or {})
    r = result.get("rag_stats") or {}
    if r.get("analyzed"):
        print("\nRAG analysis:", file=sys.stderr)
        print(f"  {r['analyzed']:>6}  candidates analyzed "
              f"({r.get('with_answers', 0)} with answers)", file=sys.stderr)
        for qid, slot in sorted((r.get("per_question") or {}).items()):
            errs = f", {slot['errors']} error(s)" if slot.get("errors") else ""
            print(f"  {slot['answered']:>6}  {qid}{errs}", file=sys.stderr)
    u = result.get("usage_totals") or {}
    print(f"\nmined {result['n_candidates']} candidate PR(s) | "
          f"provider {args.provider} | cost ${total_cost:.4f}", file=sys.stderr)
    if u.get("n_costed") and not u.get("cost_usd") and u.get("input_tokens"):
        print(f"  note: no price entry for {args.model or 'the default model'} — tokens are "
              f"counted, cost is NOT (add its rates to numbugminer/costs.py)", file=sys.stderr)
    if u.get("n_costed"):
        print(f"  {u['llm_calls']} LLM call(s), "
              f"{u['input_tokens']:,} in / {u['output_tokens']:,} out tokens | "
              f"per candidate: ${u['avg_cost_per_candidate']:.4f}, "
              f"{u['avg_calls_per_candidate']} call(s), "
              f"{u['avg_tokens_per_candidate']:,.0f} tokens", file=sys.stderr)
    print(f"saved -> {out}", file=sys.stderr)
    return 0


def _run_split_patches(args) -> int:
    from pathlib import Path

    from . import pr_source, repo as repo_mod
    from .config import PROJECT_ROOT

    cfg = load_config()
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 1
    records = data["candidates"] if isinstance(data, dict) and "candidates" in data else (
        data if isinstance(data, list) else [data])

    if getattr(args, "numerical_only", False):
        print("note: --numerical-only is ignored — the miner no longer classifies "
              "candidates; curate the set before this step", file=sys.stderr)

    out_dir = Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "workspace" / "patches"
    out_dir.mkdir(parents=True, exist_ok=True)

    index = []
    for r in records:
        ref = pr_source.parse_pr_ref(r["reference"])
        slug = f"{ref.repo}_{ref.number}"
        try:
            bundle = pr_source.fetch_pr_bundle(ref, token=cfg.github_token,
                                               include_logs=False, include_commit_tests=False)
            parts = repo_mod.split_patch(bundle.diff)
        except Exception as e:  # noqa: BLE001
            print(f"  {slug}: error {type(e).__name__}: {e}", file=sys.stderr)
            continue
        (out_dir / f"{slug}.source.patch").write_text(parts["source_patch"], encoding="utf-8")
        (out_dir / f"{slug}.test.patch").write_text(parts["test_patch"], encoding="utf-8")
        index.append({
            "pr": ref.number, "repo": f"{ref.owner}/{ref.repo}", "slug": slug,
            "buggy_commit": bundle.buggy_commit, "fixed_commit": bundle.fixed_commit,
            "source_patch": f"{slug}.source.patch", "source_files": parts["source_files"],
            "test_patch": f"{slug}.test.patch", "test_files": parts["test_files"],
        })
        print(f"  {slug}: source {len(parts['source_files'])} file(s), "
              f"test {len(parts['test_files'])} file(s)", file=sys.stderr)

    # One patches dir accumulates several repos: splitting mathjs after sympy
    # must not erase sympy's entries, since the .patch files for both are sitting
    # right there and every downstream lookup goes through this index. Entries
    # are keyed by slug, so re-splitting a PR refreshes it in place.
    idx_file = out_dir / "index.json"
    prior = []
    if idx_file.is_file():
        try:
            existing = json.loads(idx_file.read_text(encoding="utf-8"))
            prior = existing if isinstance(existing, list) else existing.get("entries", [])
        except Exception:  # noqa: BLE001 — an unreadable index is rebuilt, not merged
            prior = []
    fresh = {e["slug"] for e in index}
    merged = [e for e in prior if e.get("slug") not in fresh] + index
    idx_file.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    kept = len(merged) - len(index)
    print(f"\nsplit {len(index)} PR(s) -> {out_dir} (source.patch + test.patch each)",
          file=sys.stderr)
    print(f"index.json: {len(merged)} entr(ies) total"
          + (f" ({kept} kept from earlier splits)" if kept else ""), file=sys.stderr)
    return 0


def _strategy_usage_totals(strategies: list) -> dict:
    """What generating these strategies cost, in tokens and dollars.

    Recorded per run for the same reason mining records it: a corpus built with
    an LLM in the loop has a price, and it can only be reported later if the
    numbers are written down beside the artefact they paid for.
    """
    tallies = [s.get("usage") or {} for s in strategies if s.get("usage")]
    # the keys a `Usage` record actually carries — summing names it does not
    # have reports a corpus that cost nothing and made no calls
    total = {k: sum(t.get(k, 0) or 0 for t in tallies)
             for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                       "cache_write_tokens", "cost_usd")}
    total["llm_calls"] = len(tallies)
    total["peak_calls"] = sum(1 for t in tallies if t.get("peak"))
    total["n_costed"] = len(tallies)
    total["n_strategies"] = len(strategies)
    if tallies:
        total["cost_usd_per_strategy"] = round(total["cost_usd"] / len(tallies), 6)
    return total



#: the CSV's labels in the harness's own vocabulary — bug_type is compared and
#: printed as a lowercase token ("special_value", not "Special Value")
_CATEGORY_TOKENS = {"accuracy": "accuracy", "special value": "special_value",
                    "correctness": "correctness", "convergence": "convergence"}


def _curated_categories(path) -> dict:
    """(repo, pr) -> bug_type from a `repo,pr-link,category` CSV."""
    import csv as _csv
    import re as _re
    from pathlib import Path as _Path

    out = {}
    if not path:
        return out
    rows = _csv.DictReader(_Path(path).open(encoding="utf-8"))
    for row in rows:
        link = row.get("pr-link") or row.get("pr_link") or ""
        m = _re.search(r"/pull/(\d+)", link)
        if not m:
            continue
        label = (row.get("category") or "").strip().lower()
        out[((row.get("repo") or "").strip(), int(m.group(1)))] = _CATEGORY_TOKENS.get(
            label, label.replace(" ", "_"))
    return out


def _emit_strategies(records: list, outfile, *, provider: str, model: str,
                     host: str = "", patches_dir=None, source: str = "",
                     categories=None) -> int:
    """Write `records` as a *.strategies.json the reproducer consumes directly.

    The same payload `repro-strategy` produces, generated in-process straight
    from the answers a mine run just produced — so a corpus mined without Q1
    never has to be re-read, re-filtered, or passed through a second command
    that would look for the verdict it does not have.
    """
    import json as _json
    from pathlib import Path as _Path

    from . import providers, reproduce

    patch_by_pr: dict = {}
    if patches_dir:
        pdir = _Path(patches_dir)
        idx = pdir / "index.json"
        if idx.is_file():
            for e in _json.loads(idx.read_text(encoding="utf-8")):
                pr, tp = e.get("pr") or e.get("pr_number"), pdir / (e.get("test_patch") or "")
                if pr and e.get("test_patch") and tp.exists():
                    patch_by_pr[pr] = tp.read_text(encoding="utf-8")
        else:
            print(f"warning: no index.json in {pdir}; strategies get no test diff",
                  file=sys.stderr)

    curated = _curated_categories(categories)
    if categories:
        print(f"categories: {len(curated)} curated label(s) from {categories}",
              file=sys.stderr)

    out = []
    for r in records:
        st = reproduce.strategy(r, provider=provider, model=model, host=host,
                                test_patch=patch_by_pr.get(r.get("pr_number"), ""))
        # Q1 is where bug_type used to come from; with it dropped the curated
        # CSV supplies the category, and only when the model left it empty.
        label = curated.get((r.get("repo"), r.get("pr_number")))
        if label and not st.get("bug_type"):
            st["bug_type"] = label
            st["bug_type_source"] = str(categories)
        out.append(st)
        print(f"  #{st.get('pr_number')}: "
              f"{(st.get('strategy') or {}).get('summary') or st.get('error', '')}",
              file=sys.stderr)

    totals = _strategy_usage_totals(out)
    outfile = _Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    # a strategies file for one repo accumulates across runs, exactly as the
    # candidates file does: a re-mined PR replaces its own record, not the rest
    prior = []
    if outfile.is_file():
        try:
            raw = _json.loads(outfile.read_text(encoding="utf-8"))
            prior = raw.get("strategies", []) if isinstance(raw, dict) else raw
        except Exception:                      # noqa: BLE001 — unreadable: replaced
            prior = []
    fresh = {st.get("pr_number") for st in out}
    merged = [p for p in prior if p.get("pr_number") not in fresh] + out
    outfile.write_text(_json.dumps({
        "input": source,
        "evidence": "mined",
        "provider": provider,
        "model": model or providers.DEFAULT_CHAT_MODEL.get(provider, ""),
        "n_strategies": len(merged),
        "usage_totals": _strategy_usage_totals(merged),
        "strategies": merged,
    }, indent=2), encoding="utf-8")
    print(f"strategies -> {outfile}  ({len(out)} new, {len(merged)} total) "
          f"| {totals['llm_calls']} call(s), ${totals['cost_usd']:.4f}", file=sys.stderr)
    return len(out)


def _run_repro_strategy(args) -> int:
    from pathlib import Path

    from . import providers, reproduce

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 1
    # accept a mine output {candidates:[...]}, a bare list, or a single record
    if isinstance(data, dict) and "candidates" in data:
        records = data["candidates"]
    elif isinstance(data, list):
        records = data
    else:
        records = [data]

    if getattr(args, "numerical_only", False):
        print("note: --numerical-only is ignored — the miner no longer classifies "
              "candidates; curate the set before this step", file=sys.stderr)

    # A model that answered with prose, or ran out of budget mid-object, leaves a
    # record with an `error`. Those are worth retrying on their own — the rest of
    # the corpus is already paid for.
    previous, failed_prs = [], None
    outfile = Path(args.out) if args.out else Path(args.input).with_suffix(".strategies.json")
    if args.only_failed:
        if not outfile.exists():
            print(f"error: --only-failed needs an existing {outfile}", file=sys.stderr)
            return 1
        raw = json.loads(outfile.read_text(encoding="utf-8"))
        previous = (raw.get("strategies") if isinstance(raw, dict) and "strategies" in raw
                    else raw if isinstance(raw, list) else [raw])
        failed_prs = {p["pr_number"] for p in previous if p.get("error")}
        if not failed_prs:
            print(f"nothing to retry: every strategy in {outfile} parsed", file=sys.stderr)
            return 0
        records = [r for r in records if r.get("pr_number") in failed_prs]
        print(f"retrying {len(records)} failed strateg(ies): "
              f"{sorted(failed_prs)}", file=sys.stderr)

    if args.pr is not None:
        records = [r for r in records if r.get("pr_number") == args.pr]
    elif not args.all:
        if not records:
            print("error: no candidates to select", file=sys.stderr)
            return 1
        records = [records[args.index]]

    # Optionally load the reproducing test diff per PR so the model can extract
    # concrete input/expected-output examples from the actual assertions.
    patch_by_pr: dict = {}
    if args.patches_dir:
        pdir = Path(args.patches_dir)
        idx_file = pdir / "index.json"
        if idx_file.exists():
            for e in json.loads(idx_file.read_text(encoding="utf-8")):
                pr = e.get("pr") or e.get("pr_number")
                tp = pdir / e.get("test_patch", "")
                if pr and e.get("test_patch") and tp.exists():
                    patch_by_pr[pr] = tp.read_text(encoding="utf-8")
        else:
            print(f"warning: no index.json in {pdir}; skipping test-patch input", file=sys.stderr)

    # The no-miner arm's whole evidence base: the PR as GitHub serves it, and the
    # README at the buggy commit. Fetched here rather than in `reproduce` so a
    # failed request is reported per PR and the rest of the batch still runs.
    def pr_context_for(record: dict) -> dict:
        from . import pr_source

        cfg_token = load_config().github_token
        ref = pr_source.parse_pr_ref(record["reference"])
        # include_logs=False: CI transcripts are mined evidence, not PR content.
        # The linked issues and the discussion ARE part of the report a person
        # reading this PR would have, and carry the values the bug is about.
        bundle = pr_source.fetch_pr_bundle(ref, token=cfg_token, include_logs=False,
                                           include_commit_tests=False)
        readme = pr_source.fetch_readme(ref, sha=record.get("buggy_commit", ""),
                                        token=cfg_token)
        if not readme:
            print(f"  #{record.get('pr_number')}: no README at this commit — "
                  f"the arm runs without one for this instance", file=sys.stderr)
        issues = [{"number": i.number, "title": i.title, "body": i.body}
                  for i in bundle.linked_issues]
        # The LINKED ISSUES' discussion only. A pull request's own thread is
        # process, not evidence — on xarray#11478 it is two bot messages
        # ("Thank you for opening this pull request!", "Congratulations on
        # completing your first pull request!") — while the issue thread is
        # where a reporter posts the value they actually got.
        comments = [c for i in bundle.linked_issues for c in i.comments]
        return {"title": bundle.title, "body": bundle.body, "diff": bundle.diff,
                "readme": readme, "issues": issues, "comments": comments}

    out = []
    for r in records:
        tp_text = patch_by_pr.get(r.get("pr_number"), "")
        ctx = None
        if args.from_pr_readme:
            try:
                ctx = pr_context_for(r)
            except Exception as e:  # noqa: BLE001 — one unreachable PR, not the batch
                print(f"  #{r.get('pr_number')}: cannot fetch PR/README "
                      f"({type(e).__name__}: {e}) — skipped", file=sys.stderr)
                continue
        s = reproduce.strategy(r, provider=args.provider, model=args.model, host=args.host,
                               test_patch=tp_text, pr_context=ctx)
        out.append(s)
        st = s.get("strategy", {})
        print(f"  #{s['pr_number']}: {st.get('summary') or s.get('error', '')}", file=sys.stderr)

    if args.only_failed:
        # keep the corpus in its original order; replace only what was retried
        fresh = {s["pr_number"]: s for s in out}
        merged = [fresh.pop(p["pr_number"], p) for p in previous]
        out = merged + list(fresh.values())
        still = [s["pr_number"] for s in out if s.get("error")]
        print(f"{len(failed_prs) - len(still)} of {len(failed_prs)} recovered"
              + (f"; still failing: {sorted(still)}" if still else ""), file=sys.stderr)

    totals = _strategy_usage_totals(out)
    payload = {
        "input": str(args.input),
        "provider": args.provider,
        "model": args.model or providers.DEFAULT_CHAT_MODEL.get(args.provider, ""),
        "n_strategies": len(out),
        "usage_totals": totals,
        "strategies": out,
    }
    outpath = outfile
    outpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2))
    print(f"\ngenerated {len(out)} strategy(ies) | provider {args.provider} "
          f"| {totals['llm_calls']} call(s), {totals['input_tokens']:,} in "
          f"({totals['cache_read_tokens']:,} cached) / {totals['output_tokens']:,} out "
          f"| cost ${totals['cost_usd']:.4f}", file=sys.stderr)
    print(f"saved -> {outpath}", file=sys.stderr)
    return 0


def _run_taxonomy(args) -> int:
    from collections import Counter
    from pathlib import Path

    from . import taxonomy as tax

    tfields = [f.strip() for f in args.text_fields.split(",") if f.strip()]

    def id_of(r):
        return str(r.get(args.id_field) or r.get("instance_id") or r.get("key")
                   or r.get("id") or "?")

    def text_of(r):
        parts = []
        for f in tfields:
            v = r.get(f)
            if isinstance(v, list):
                v = "\n".join(str(x) for x in v)
            if v:
                parts.append(str(v))
        return "\n\n".join(parts)

    try:
        rows = [json.loads(l) for l in open(args.input, encoding="utf-8") if l.strip()]
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot read {args.input}: {e}", file=sys.stderr)
        return 1

    out, cost = [], 0.0
    for r in rows:
        if args.flagged_only and "is_numerical_bug" in r and not r.get("is_numerical_bug"):
            continue
        if args.limit is not None and len(out) >= args.limit:
            break
        res = tax.classify(text_of(r), provider=args.provider, model=args.model, host=args.host)
        cost += res.usage.cost_usd if res.usage else 0.0
        out.append({"id": id_of(r), "keyword_bug_type": r.get("bug_type", ""), **res.to_dict()})
        if len(out) % 25 == 0:
            print(f"  {len(out)} classified (${cost:.3f})", file=sys.stderr)

    outpath = Path(args.out) if args.out else Path(args.input).with_suffix(".taxonomy.json")
    outpath.write_text(json.dumps(out, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(out, indent=2))
    confirmed = sum(1 for r in out if r["is_numerical_bug"])
    print(f"\nclassified {len(out)} | LLM-confirmed numerical {confirmed} | cost ${cost:.3f}",
          file=sys.stderr)
    print("by category: " + ", ".join(f"{k}:{v}" for k, v in
          Counter(r["category"] for r in out).most_common()), file=sys.stderr)
    print("by leaf:", file=sys.stderr)
    for lf, c in Counter(r["leaf"] for r in out).most_common():
        print(f"  {c:4}  {lf}", file=sys.stderr)
    print(f"saved -> {outpath}", file=sys.stderr)
    return 0


def _run_defects4j(args) -> int:
    from .defects4j_eval import TEXT_FIELDS as D4J_FIELDS
    from .defects4j_eval import evaluate, load_bugs, summarize
    from .keyword_filter import DEFAULT_THRESHOLD

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    bad = [f for f in fields if f not in D4J_FIELDS]
    if bad:
        print(f"error: unknown field(s) {bad}; available: {', '.join(D4J_FIELDS)}", file=sys.stderr)
        return 1
    threshold = args.threshold if args.threshold is not None else DEFAULT_THRESHOLD
    projects = [p.strip() for p in args.projects.split(",")] if args.projects else None

    try:
        bugs = load_bugs(args.source, projects=projects, limit=args.limit,
                         progress=lambda m: print(f"  {m}", file=sys.stderr))
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not bugs:
        print("error: loaded 0 bugs", file=sys.stderr)
        return 1

    results = evaluate(bugs, fields=fields, threshold=threshold)
    summary = summarize(results)

    if args.out:
        from pathlib import Path

        out = Path(args.out)
        with out.open("w", encoding="utf-8") as fh:
            for r in results:
                fh.write(json.dumps(r.to_dict()) + "\n")
        print(f"wrote {len(results)} verdicts -> {out}", file=sys.stderr)

    shown = [r for r in results if r.is_numerical_bug] if args.flagged_only else results
    if args.json:
        print(json.dumps({"summary": summary, "results": [r.to_dict() for r in shown]}, indent=2))
        return 0

    for r in shown:
        mark = "N" if r.is_numerical_bug else "-"
        print(f"[{mark}] {r.key:<14} {r.bug_type:<12} conf={r.confidence:.2f} "
              f"score={r.score:g}  {', '.join(r.keywords)}")
    print(f"\nscanned fields: {', '.join(fields)}  | threshold: {threshold:g}", file=sys.stderr)
    print(f"flagged {summary['flagged']}/{summary['total']} "
          f"({summary['flag_rate'] * 100:.1f}%) as numerical bugs", file=sys.stderr)
    if summary["bug_types"]:
        print("bug types: " + ", ".join(f"{k}:{v}" for k, v in summary["bug_types"].items()),
              file=sys.stderr)
    if summary["by_project"]:
        bp = ", ".join(f"{p}:{d['flagged']}/{d['total']}" for p, d in summary["by_project"].items())
        print(f"by project (flagged/total): {bp}", file=sys.stderr)
    if summary["keyword_freq"]:
        top = ", ".join(f"{k}:{v}" for k, v in list(summary["keyword_freq"].items())[:10])
        print(f"top keywords: {top}", file=sys.stderr)
    return 0


def _print_probe(report) -> None:
    print(f"reference : {report.reference}")
    print(f"repo      : {report.repo}")
    print(f"commits   : buggy {(report.buggy_commit or '?')[:12]} -> "
          f"fixed {(report.fixed_commit or '?')[:12]}")
    print(f"provider  : {report.provider} | model: {report.model} | chunks: {report.n_chunks}")
    if report.test_patch is not None:
        tp = report.test_patch
        status = "applied" if tp.get("applied") else f"FAILED ({tp.get('error','')[:80]})"
        print(f"test patch: {status} -> {', '.join(tp.get('files', [])) or '(none)'}")
    if report.note:
        print(f"note      : {report.note}")
    for a in report.answers:
        print(f"\n[{a.question_id}] {a.question}")
        if a.error:
            print(f"    error: {a.error}")
            continue
        for key, val in a.data.items():
            if isinstance(val, dict):
                val = f"{val.get('answer')} ({val.get('evidence','')[:80]})"
            elif isinstance(val, list):
                val = "; ".join(str(x) for x in val)[:200] if val else "(none)"
            print(f"    {key}: {str(val)[:200]}")
        if a.grounded_on:
            print(f"    grounded_on: {', '.join(dict.fromkeys(a.grounded_on))[:160]}")
    if report.usage:
        u = report.usage
        print(f"\ncost      : ${u['cost_usd']:.4f} ({u['model']} | "
              f"in {u['input_tokens']}, out {u['output_tokens']})")


def _run_probe(args) -> int:
    from . import probe

    cfg = load_config()
    only = [q.strip() for q in args.only.split(",")] if args.only else None
    # Embeddings default to the generation provider; neither Anthropic nor
    # DeepSeek publishes an embedding API, so that silently drops retrieval to
    # the TF-IDF fallback. Say so rather than letting the answers quietly worsen.
    embedder_provider = args.embed_provider or args.provider
    if embedder_provider in ("anthropic", "deepseek"):
        print(f"note: {embedder_provider} has no embedding API — retrieval will use the "
              f"local TF-IDF fallback. Pass --embed-provider ollama for dense embeddings.",
              file=sys.stderr)
    try:
        report = probe.probe_pr(
            cfg, args.reference, repo=args.repo,
            provider=args.provider, model=args.model, embed_model=args.embed_model,
            host=args.host, work_dir=args.work_dir, clone=not args.no_clone,
            require_merged=not args.allow_unmerged,
            apply_test_patch=args.apply_test_patch, only=only,
        )
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_probe(report)
    saved = probe.save_report(report, args.out)
    print(f"\nsaved report -> {saved}", file=sys.stderr)
    return 0


def _run_probe_compare(args) -> int:
    from pathlib import Path

    from . import probe

    if len(args.reports) < 2:
        print("error: give at least two probe report JSON files", file=sys.stderr)
        return 1
    reports = []
    for p in args.reports:
        try:
            reports.append(json.loads(Path(p).read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"error: cannot read {p}: {e}", file=sys.stderr)
            return 1
    comp = probe.compare_reports(reports)
    if args.json or args.out:
        payload = json.dumps(comp, indent=2)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
            print(f"saved comparison -> {args.out}", file=sys.stderr)
        if args.json:
            print(payload)
    if not args.json:
        for i, (lab, ref) in enumerate(zip(comp["labels"], comp["references"])):
            print(f"[{i+1}] {lab}: {ref}")
        print()
        print(probe.format_comparison(comp))
    return 0


#: Human labels for the pre-RAG funnel, in pipeline order.
_FUNNEL_LABELS = (
    ("scanned", "closed PRs scanned"),
    ("in_date_window", "within the date window"),
    ("not_dependency_bump", "after the dependency/bump filter"),
    ("merged", "merged"),
    ("numerical_keywords", "numerical keywords present"),
    ("bug_fix_intent", "bug-fixing intent present"),
    ("code_change", "changed code (not manifests only)"),
    ("modifies_source", "modifies source too (a fix to reproduce against)"),
    ("touches_tests", "touches a test"),
    ("test_writing_commit", "has a test-writing commit"),
)


def _print_funnel(stats: dict, out=None) -> None:
    """The pre-RAG funnel: what each stage saw and how many it passed."""
    out = out or sys.stderr
    if not stats:
        return
    window = stats.get("window") or {}
    span = " ".join(f"{k}={v}" for k, v in window.items() if v)
    print(f"\npre-RAG filtering{' (' + span + ')' if span else ''}:", file=out)
    prev = None
    for key, label in _FUNNEL_LABELS:
        if key not in stats:
            continue
        n = stats[key]
        drop = f"  (-{prev - n})" if prev is not None and prev >= n else ""
        print(f"  {n:>6}  {label}{drop}", file=out)
        prev = n
    if "kept" in stats:
        print(f"  {stats['kept']:>6}  candidates handed to RAG", file=out)
    why = {
        "date_boundary": "scan stopped at the --since boundary (window fully covered)",
        "scan_cap": "SCAN CAP REACHED — the window is NOT fully covered; raise --scan-cap "
                    "(0 = unlimited) or the corpus describes only the newest PRs scanned",
        "limit": "stopped at --limit (window not fully covered)",
        "exhausted": "the repository's closed PRs were exhausted",
    }.get(stats.get("scan_stopped"))
    if why:
        print(f"          {why}", file=out)


def _scan_cap(args):
    """The paging budget: the date is the intended bound when one is given, so a
    dated crawl is unlimited unless the cap is set explicitly."""
    if args.scan_cap is None:
        return None if args.since else 1000
    return None if args.scan_cap <= 0 else args.scan_cap


def _run_crawl(args) -> int:
    from .crawler import crawl_closed_prs

    cfg = load_config()
    funnel: dict = {}
    try:
        prs = crawl_closed_prs(
            args.repo, cfg.github_token,
            only_merged=args.merged,
            keyword_prefilter=args.keyword_filter,
            require_test_commits=args.test_commits,
            strict=args.strict,
            require_bug_intent=not args.no_bug_intent,
            require_source_change=not args.allow_test_only,
            exclude_bumps=not args.include_bumps,
            commit_cap=args.commit_cap,
            since=args.since, until=args.until, scan_cap=_scan_cap(args),
            limit=args.limit if args.limit is not None else (None if args.since else 20),
            stats=funnel,
        )
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([p.to_dict() for p in prs], indent=2))
    else:
        for p in prs:
            mark = "merged" if p.merged else "closed"
            kw = f"  [{', '.join(p.numerical_indicators)}]" if p.numerical_indicators else ""
            bug = f"  ({', '.join(p.bug_indicators)})" if p.bug_indicators else ""
            print(f"#{p.number:<6} {mark}  {p.title}{kw}{bug}")
            if p.test_commits:
                cases = f" :: {', '.join(p.test_cases[:3])}" if p.test_cases else ""
                print(f"         tests: {len(p.test_commits)} commit(s) -> "
                      f"{', '.join(p.test_files)}{cases}")
        filters = []
        if args.test_commits:
            filters.append("numerical + test-writing")
        elif args.keyword_filter:
            filters.append("keyword-filtered")
        if args.strict and (args.test_commits or args.keyword_filter):
            filters.append("strict")
        if (args.test_commits or args.keyword_filter) and not args.no_bug_intent:
            filters.append("bug-intent")
        if args.merged:
            filters.append("merged-only")
        suffix = f" ({', '.join(filters)})" if filters else ""
        print(f"\n{len(prs)} closed PR(s) for {args.repo}{suffix}", file=sys.stderr)
        _print_funnel(funnel)
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.cmd == "crawl":
        return _run_crawl(args)

    if args.cmd in ("swebench", "swtbench"):  # SWT-bench reuses the SWE-bench eval
        return _run_swebench(args)

    if args.cmd == "defects4j":
        return _run_defects4j(args)

    if args.cmd == "taxonomy":
        return _run_taxonomy(args)

    if args.cmd == "split-patches":
        return _run_split_patches(args)

    if args.cmd == "repro-strategy":
        return _run_repro_strategy(args)

    if args.cmd == "mine":
        return _run_mine_logged(args)

    if args.cmd == "probe":
        return _run_probe(args)

    if args.cmd == "probe-compare":
        return _run_probe_compare(args)

    if args.cmd == "seed-gen":
        return _run_seed_gen(args)

    if args.cmd != "analyze":
        build_parser().error("unknown command")

    if not args.reference and not args.log_file:
        build_parser().error("give a PR/run reference or --log-file")

    cfg = load_config()
    if args.corpus:
        import dataclasses
        from pathlib import Path

        cfg = dataclasses.replace(cfg, seed_corpus_path=Path(args.corpus))
    try:
        report = analyze(
            cfg,
            args.reference,
            repo=args.repo,
            job_id=args.job_id,
            log_file=args.log_file,
            force_pr=args.pr,
            include_logs=args.with_logs,
            include_commit_tests=not args.no_commit_tests,
            run_llm=not args.no_llm,
            method="keyword" if args.keyword else "rag",
            require_merged=not args.allow_unmerged,
        )
    except Exception as e:  # noqa: BLE001 — surface a clean message, not a traceback
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

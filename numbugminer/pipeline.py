from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from . import assemble, classifier, downloader, keyword_filter, preprocess, pr_source
from .config import CACHE_DIR, Config
from .knowledge_base import load_corpus
from .retriever import Index, Retrieved


@dataclass
class Report:
    reference: str
    excerpt: str
    signals: list[str]
    retrieved: list[dict]
    source: str = "log"          # "pr" | "log"
    pr: dict | None = None        # PRBundle.to_dict() when source == "pr"
    verdict: dict | None = None
    usage: dict | None = None     # token usage + USD cost of the Claude verdict
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _retrieval_payload(retrieved: list[Retrieved]) -> list[dict]:
    return [
        {
            "id": r.example.id,
            "label": r.example.label,
            "bug_type": r.example.bug_type,
            "score": round(r.score, 3),
        }
        for r in retrieved
    ]


def _retrieve_and_classify(
    cfg: Config,
    reference: str,
    *,
    source: str,
    excerpt: str,
    signals: list[str],
    query: str,
    pr: dict | None,
    note_empty: str,
    run_llm: bool,
    method: str = "rag",
) -> Report:
    """Shared tail: classify the excerpt via the RAG or keyword-filter path."""
    if not excerpt:
        return Report(reference=reference, excerpt="", signals=[], retrieved=[],
                      source=source, pr=pr, note=note_empty)

    # --- Keyword-filter path: no corpus, no embeddings, no model call --------
    if method == "keyword":
        verdict = keyword_filter.classify(excerpt)
        return Report(
            reference=reference,
            excerpt=excerpt,
            signals=signals,
            retrieved=[],
            source=source,
            pr=pr,
            verdict=verdict.model_dump(),
            note="Keyword-filter run (--keyword); RAG-free, no Claude verdict.",
        )

    corpus = load_corpus(cfg.seed_corpus_path)
    index = Index.build(corpus, cfg.embed_model, cfg.embed_backend, cache_dir=CACHE_DIR)
    retrieved = index.search(query or excerpt, top_k=cfg.top_k)

    report = Report(
        reference=reference,
        excerpt=excerpt,
        signals=signals,
        retrieved=_retrieval_payload(retrieved),
        source=source,
        pr=pr,
    )

    if not run_llm:
        report.note = "Retrieval-only run (--no-llm); no Claude verdict."
        return report

    import anthropic

    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY
    verdict, usage = classifier.classify(client, cfg.model, excerpt, retrieved, set(signals))
    report.verdict = verdict.model_dump()
    report.usage = usage.to_dict()
    return report


def analyze_pr(
    cfg: Config,
    reference: str,
    *,
    repo: str | None = None,
    include_logs: bool = False,
    include_commit_tests: bool = True,
    run_llm: bool = True,
    method: str = "rag",
    require_merged: bool = True,
) -> Report:
    ref = pr_source.parse_pr_ref(reference, repo=repo)
    bundle = pr_source.fetch_pr_bundle(
        ref, token=cfg.github_token,
        include_logs=include_logs, include_commit_tests=include_commit_tests,
    )

    # Only MERGED PRs are analysed by default: a numerical-bug benchmark instance
    # needs the fix that actually landed, which gives a settled buggy -> fixed
    # commit pair (merge commit vs its first parent). A closed-but-unmerged PR
    # was rejected — its "fix" never landed — and an open PR is still in flux, so
    # both are skipped unless the caller opts in with require_merged=False.
    if require_merged and not bundle.merged:
        state = "merged" if bundle.merged else (bundle.state or "open")
        return Report(
            reference=ref.html_url,
            excerpt="",
            signals=[],
            retrieved=[],
            source="pr",
            pr=bundle.to_dict(),
            note=(f"PR is {state}, not merged — skipped. Only merged PRs have a "
                  "landed fix (settled buggy/fixed commit pair). Pass "
                  "--allow-unmerged to analyze it anyway."),
        )

    excerpt, signals, query = assemble.assemble_evidence(bundle, cfg.max_excerpt_chars)
    return _retrieve_and_classify(
        cfg, ref.html_url,
        source="pr",
        excerpt=excerpt,
        signals=signals,
        query=query,
        pr=bundle.to_dict(),
        note_empty="PR fetched but produced no analysable evidence.",
        run_llm=run_llm,
        method=method,
    )


def get_log(cfg: Config, reference: str, repo: str | None, job_id: str | None,
            log_file: str | None) -> str:
    if log_file:
        return Path(log_file).read_text(encoding="utf-8", errors="replace")
    target = downloader.parse_target(reference, repo=repo, job_id=job_id)
    return downloader.download_logs(target, token=cfg.github_token)


def analyze_log(
    cfg: Config,
    reference: str,
    *,
    repo: str | None = None,
    job_id: str | None = None,
    log_file: str | None = None,
    run_llm: bool = True,
    method: str = "rag",
) -> Report:
    log = get_log(cfg, reference, repo, job_id, log_file)
    regions = preprocess.extract_regions(log)
    signals = sorted(preprocess.numerical_signals(regions))
    excerpt = preprocess.build_excerpt(regions, cfg.max_excerpt_chars)
    return _retrieve_and_classify(
        cfg, reference,
        source="log",
        excerpt=excerpt,
        signals=signals,
        query=excerpt,
        pr=None,
        note_empty="No error or numerical-signal regions found in the log.",
        run_llm=run_llm,
        method=method,
    )


def analyze(
    cfg: Config,
    reference: str,
    *,
    repo: str | None = None,
    job_id: str | None = None,
    log_file: str | None = None,
    force_pr: bool = False,
    include_logs: bool = False,
    include_commit_tests: bool = True,
    run_llm: bool = True,
    method: str = "rag",
    require_merged: bool = True,
) -> Report:
    """Dispatch to the PR path or the log path based on the reference."""
    if log_file:
        return analyze_log(cfg, reference, repo=repo, job_id=job_id,
                           log_file=log_file, run_llm=run_llm, method=method)
    # PR path for PR URLs / owner/repo#N, or a bare number forced with --pr.
    if force_pr or pr_source.is_pr_ref(reference):
        return analyze_pr(cfg, reference, repo=repo,
                          include_logs=include_logs,
                          include_commit_tests=include_commit_tests,
                          run_llm=run_llm, method=method,
                          require_merged=require_merged)
    return analyze_log(cfg, reference, repo=repo, job_id=job_id,
                       run_llm=run_llm, method=method)

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import keyword_filter, providers
from .chroma_store import Chunk, ChromaStore
from .docstore import _hunks, _split
from .pr_source import PRBundle, is_test_file

_HUNK_HDR = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# --- structured questions ----------------------------------------------------

@dataclass(frozen=True)
class MineQuestion:
    id: str
    text: str
    query: str
    sources: tuple[str, ...]
    schema: dict
    k: int = 6


def _num(props, required):
    return {"type": "object", "additionalProperties": False,
            "properties": props, "required": required}


# The four numerical-bug categories (+ not_numerical for keyword false positives).
_CATEGORY = ["accuracy", "special_value", "convergence", "correctness", "not_numerical"]
_STR = {"type": "string"}
_STRLIST = {"type": "array", "items": {"type": "string"}}
_NAMEVER = {"type": "array", "items": {
    "type": "object", "additionalProperties": False,
    "properties": {"name": _STR, "version": _STR}, "required": ["name", "version"]}}

#: The three questions of the RAG stage, each answered from retrieved evidence
#: alone. There is no classification question and no gate: deciding whether a PR
#: is a numerical bug is a CURATION step that happens outside the miner (the
#: crawler's keyword prescreen, then human annotation), so every candidate handed
#: to this stage is answered. The bug category comes from the curated CSV, not
#: from a model verdict.
QUESTIONS: list[MineQuestion] = [
    # --- Q1: the behavioural ground truth reproduction is validated against ---
    MineQuestion("behavior",
        "What input or execution condition triggers the numerical bug, what "
        "behaviour or output occurs in the BUGGY revision, and what is expected "
        "after the fix? The assertions this PR added to its tests are supplied "
        "below as authoritative facts — an assertion states the input, the "
        "property checked and the expected value directly, so prefer it over any "
        "paraphrase in the discussion and use the retrieved passages to explain "
        "what the buggy revision produced instead. Give expressions exactly as "
        "they would be evaluated, and attribute every example to its source.",
        "input that triggers the bug reproduction steps observed wrong output NaN "
        "expected correct value assertion expected actual tolerance example",
        ("issue", "discussion", "test_diff", "pr_body", "diff", "web"),
        _num({"triggering_input": _STR, "triggering_condition": _STR,
              "buggy_behavior": _STR, "expected_behavior": _STR,
              "input_output_examples": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False,
                  "properties": {"input": _STR, "buggy_output": _STR,
                                 "expected_output": _STR, "source": _STR},
                  "required": ["input", "buggy_output", "expected_output", "source"]}},
              "confidence": {"type": "number"}},
             ["triggering_input", "triggering_condition", "buggy_behavior",
              "expected_behavior", "input_output_examples", "confidence"])),

    # --- Q2: the tests that reproduce or validate it -------------------------
    MineQuestion("tests",
        "Which tests reproduce or validate this bug, and how are they executed? "
        "Name the test files and the individual test cases, say whether this PR "
        "introduced them or modified existing ones, and give the selector or "
        "command that runs exactly those cases.",
        "test file test case added modified reproduces the bug how to run a single "
        "test selector pytest cargo test mocha maven junit",
        ("test_diff", "diff", "ci_yaml", "issue", "pr_body"),
        _num({"test_files": _STRLIST, "test_cases": _STRLIST,
              "introduced_or_modified": {"type": "string",
                                         "enum": ["introduced", "modified", "both", "neither"]},
              "test_selector": _STR, "test_command": _STR,
              "expected_failing_behavior": _STR, "confidence": {"type": "number"}},
             ["test_files", "test_cases", "introduced_or_modified", "test_selector",
              "test_command", "expected_failing_behavior", "confidence"])),

    # --- Q3: how to build it and reproduce both revisions --------------------
    MineQuestion("build_repro",
        "How is this repository built, and what is required to run the tests so "
        "that the buggy and fixed behaviours can be reproduced? Cover the language "
        "and its runtime or compiler version, dependencies and their versions, "
        "system packages, environment variables, configuration files, external "
        "services, and the build and test commands. Prefer what the CI workflow "
        "actually runs over what the README says when they disagree, and note the "
        "disagreement.",
        "build from source install dependencies compiler runtime version python "
        "java node cargo environment variables configuration external services "
        "system packages build command test command CI workflow",
        ("ci_yaml", "readme", "build_config", "lockfile", "web"),
        _num({"language": _STR, "compiler": _STR, "runtime_version": _STR,
              "dependency_versions": _NAMEVER, "system_packages": _STRLIST,
              "env_vars": _STRLIST, "config_files": _STRLIST,
              "external_services": _STRLIST, "build_commands": _STRLIST,
              "test_commands": _STRLIST, "conflicts": _STR,
              "confidence": {"type": "number"}},
             ["language", "compiler", "runtime_version", "dependency_versions",
              "system_packages", "env_vars", "config_files", "external_services",
              "build_commands", "test_commands", "conflicts", "confidence"]), k=8),
]


_SYSTEM = (
    "You are a numerical-software reproducibility analyst. Answer ONLY from the "
    "retrieved CONTEXT (with provenance tags) and EXTRACTED FACTS provided; do "
    "not use outside knowledge. If the context does not support an answer, use "
    "empty strings/arrays and low confidence rather than guessing. A numerical "
    "bug is a defect where floating-point computation goes wrong "
    "(NaN/Inf/overflow/underflow/precision loss/instability/non-convergence/"
    "data-type), not an environment, API, or pure-logic fault. Return a single "
    "JSON object matching the schema."
)


# --- structure-aware document building ---------------------------------------

def _md_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown/rst-ish text into (heading, body) sections."""
    sections, heading, buf = [], "", []
    for line in (text or "").splitlines():
        if re.match(r"^#{1,6}\s+\S", line) or re.match(r"^={3,}\s*$", line):
            if buf:
                sections.append((heading, "\n".join(buf)))
            heading = line.lstrip("# ").strip()[:120]
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections.append((heading, "\n".join(buf)))
    return sections or [("", text or "")]


def build_documents(bundle: PRBundle, repo_files: list, web_docs: list | None = None) -> list[Chunk]:
    """Assemble provenance-tagged chunks from the PR bundle, the cloned repo files,
    and any Web pages those artifacts directly referenced."""
    r = bundle.ref
    base = {"repo": f"{r.owner}/{r.repo}", "pr_number": r.number,
            "commit_sha": bundle.buggy_commit or "", "heading": "",
            "file_path": "", "line_start": 0, "line_end": 0}
    chunks: list[Chunk] = []
    n = 0

    def emit(source, text, **meta):
        nonlocal n
        for piece in _split(text):
            m = {**base, "source_type": source, **meta}
            chunks.append(Chunk(id=f"{source}-{n}", text=piece, metadata=m))
            n += 1

    emit("pr_meta", f"PULL REQUEST {r.owner}/{r.repo}#{r.number} "
         f"({'merged' if bundle.merged else bundle.state})\nTITLE: {bundle.title}\n"
         f"AUTHOR: {bundle.author} | base: {bundle.base_ref} | labels: {', '.join(bundle.labels)}",
         file_path=f"PR#{r.number}")
    emit("pr_body", (bundle.title + "\n\n" + bundle.body), file_path=f"PR#{r.number}")

    for li in bundle.linked_issues:
        emit("issue", f"ISSUE #{li.number} ({li.relation}, {li.state})\nTITLE: {li.title}\n{li.body}",
             file_path=f"issue#{li.number}", heading=li.title[:120])
        for c in li.comments[:6]:
            emit("issue", c, file_path=f"issue#{li.number}", heading="comment")

    for c in bundle.issue_comments[:12]:
        emit("discussion", c, file_path=f"PR#{r.number}", heading="pr comment")
    for rv in bundle.review_bodies[:12]:
        emit("discussion", rv, file_path=f"PR#{r.number}", heading="review")
    for rc in bundle.review_comments[:20]:
        emit("discussion", rc.get("body", "") + "\n" + rc.get("diff_hunk", ""),
             file_path=rc.get("path", ""), heading="inline review")

    if bundle.commit_messages:
        emit("commit", "\n\n".join(bundle.commit_messages), file_path="commits")

    for f in bundle.files:
        fname = f.get("filename", "")
        src = "test_diff" if is_test_file(fname) else "diff"
        for h in _hunks(f.get("patch", "")):
            m = _HUNK_HDR.match(h)
            ls = int(m.group(1)) if m else 0
            le = ls + (int(m.group(2)) - 1 if m and m.group(2) else 0)
            # Split large hunks so no chunk exceeds the embedder's context window.
            for piece in _split(f"--- {fname} ---\n{h}"):
                chunks.append(Chunk(id=f"{src}-{n}", text=piece,
                                    metadata={**base, "source_type": src, "file_path": fname,
                                              "line_start": ls, "line_end": le}))
                n += 1

    for wd in (web_docs or []):
        # the origin travels with the text: an answer grounded on a fetched page
        # can be traced back to the artifact that cited it
        emit("web", f"# REFERENCED PAGE: {wd.url}\n# CITED BY: {wd.origin}\n"
                    f"# TITLE: {wd.title}\n{wd.text}",
             file_path=wd.url, heading=wd.title[:120])

    for rf in (repo_files or []):
        if rf.role == "readme":
            for heading, body in _md_sections(rf.text):
                emit("readme", f"# FILE: {rf.path}\n{body}", file_path=rf.path, heading=heading)
        else:
            emit(rf.role, f"# FILE: {rf.path}\n{rf.text}", file_path=rf.path)

    return [c for c in chunks if c.text.strip()]


# --- answering ---------------------------------------------------------------

def _extra_facts(qid: str, bundle: PRBundle, repo_files: list) -> str:
    from .probe import added_test_symbols, parse_dependency_pins

    if qid == "tests":
        syms = added_test_symbols(bundle)
        if syms:
            return "ADDED/MODIFIED TEST SYMBOLS (form the selector from these):\n" + "\n".join(syms[:25])
    if qid == "behavior":
        # The triggering input and both behaviours are written down verbatim in
        # the assertions the PR added — `assert sec(z).is_zero is False` states
        # the input, the expected output, and by implication the buggy one.
        # Retrieval returns hunks; this hands over the assertion lines themselves.
        added = []
        for f in bundle.files:
            if not is_test_file(f.get("filename", "")):
                continue
            for line in (f.get("patch") or "").splitlines():
                if line.startswith("+") and not line.startswith("+++") and line[1:].strip():
                    added.append(line[1:].rstrip())
        if added:
            return ("ASSERTIONS THIS PR ADDED TO ITS TESTS (the concrete input and "
                    "expected output are stated here):\n" + "\n".join(added[:60]))
    if qid == "build_repro":
        pins, source = parse_dependency_pins(repo_files)
        if pins:
            return (f"EXTRACTED DEPENDENCY PINS (resolution: {source}):\n"
                    + "\n".join(f"{p['name']} {p['version']}" for p in pins[:50]))
    return ""


@dataclass
class Answer:
    question_id: str
    data: dict
    grounded_on: list[str] = field(default_factory=list)
    error: str = ""


def answer(q: MineQuestion, store: ChromaStore, bundle: PRBundle, repo_files: list,
           *, provider: str, model: str, host: str,
           schema: dict | None = None, extra_note: str = ""):
    hits = store.query_balanced(q.query, k=q.k, source_types=q.sources)
    ctx, refs = [], []
    for h in hits:
        m = h["metadata"]
        tag = f"{m.get('source_type')}:{m.get('file_path') or ''}"
        if m.get("line_start"):
            tag += f":{m['line_start']}-{m['line_end']}"
        ctx.append(f"[{tag} sim={h['score']}]\n{h['text']}")
        refs.append(tag)
    extra = _extra_facts(q.id, bundle, repo_files)
    extra = "\n".join(x for x in (extra, extra_note) if x)
    extra_block = f"\n\nEXTRACTED FACTS (authoritative):\n{extra}" if extra else ""
    user = (f"QUESTION: {q.text}\n\nCONTEXT PASSAGES:\n" + "\n\n---\n\n".join(ctx)
            + extra_block + "\n\nAnswer as a JSON object per the schema, grounded only in the above.")
    usage = None
    try:
        res = providers.chat(provider, model, _SYSTEM, user, schema=schema or q.schema,
                             host=host, max_tokens=900)
        usage = res.usage
        return Answer(q.id, providers.extract_json(res.text), refs), usage
    except Exception as e:  # noqa: BLE001
        return Answer(q.id, {}, refs, error=f"{type(e).__name__}: {e}"), usage


def rag_stats(candidates: list) -> dict:
    """What the RAG stage made of the candidates it was handed.

    With no classification question there is no survival rate to report: every
    candidate is answered, so what matters is how many answers came back and how
    many questions failed to parse.
    """
    answered = errors = 0
    per_question: dict = {}
    for c in candidates:
        answers = c.get("answers") or {}
        answered += 1 if answers else 0
        for qid, a in answers.items():
            slot = per_question.setdefault(qid, {"answered": 0, "errors": 0})
            slot["answered"] += 1
            if (a or {}).get("error"):
                slot["errors"] += 1
                errors += 1
    return {
        "analyzed": len(candidates),
        "with_answers": answered,
        "question_errors": errors,
        "per_question": per_question,
    }


def usage_totals(candidates: list) -> dict:
    """Aggregate mining cost over a set of candidate records.

    Reported per run so the design can be costed: what the whole corpus took and
    what one candidate costs on average. Every candidate now pays for every
    question — there is no early exit to account for.
    """
    tallies = [c.get("usage") or {} for c in candidates if c.get("usage")]
    n = len(tallies)
    total = {k: sum(t.get(k, 0) or 0 for t in tallies)
             for k in ("llm_calls", "input_tokens", "uncached_input_tokens",
                       "output_tokens", "cached_input_tokens", "peak_calls",
                       "cost_usd", "cost_usd_billed_estimate")}
    return {
        "n_records": len(candidates),
        "n_costed": n,
        "llm_calls": total["llm_calls"],
        "input_tokens": total["input_tokens"],
        "output_tokens": total["output_tokens"],
        "uncached_input_tokens": total["uncached_input_tokens"],
        "cached_input_tokens": total["cached_input_tokens"],
        "peak_calls": total["peak_calls"],
        # the quotable number is the upper bound; the estimate is what the
        # account was charged given when the calls ran and what was cached
        "cost_usd": round(total["cost_usd"], 6),
        "cost_usd_billed_estimate": round(total["cost_usd_billed_estimate"], 6),
        "avg_cost_per_candidate": round(total["cost_usd"] / n, 6) if n else 0.0,
        "avg_calls_per_candidate": round(total["llm_calls"] / n, 2) if n else 0.0,
        "avg_tokens_per_candidate": round((total["input_tokens"] + total["output_tokens"]) / n, 1) if n else 0.0,
    }


# --- orchestration -----------------------------------------------------------

@dataclass
class PRRecord:
    reference: str
    repo: str
    pr_number: int
    linked_issues: list[int]
    buggy_commit: str
    fixed_commit: str
    modified_files: list[str]
    test_files: list[str]
    keyword_signal: list[str]           # full-evidence keyword scan (body+issues+diff)
    answers: dict = field(default_factory=dict)
    web_documents: list = field(default_factory=list)   # referenced pages indexed
    n_chunks: int = 0
    usage: dict | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("reference", "repo", "pr_number", "linked_issues", "buggy_commit",
                 "fixed_commit", "modified_files", "test_files", "keyword_signal",
                 "web_documents", "n_chunks", "usage", "note", "answers")}


def mine_pr(cfg, reference: str, *, repo: str | None = None,
            provider: str = "ollama", model: str | None = None,
            embed_provider: str | None = None, embed_model: str | None = None,
            host: str = providers.OLLAMA_HOST, work_dir=None, chroma_dir=None,
            require_merged: bool = True, only: list[str] | None = None,
            cleanup_clone: bool = True,
            fetch_web: bool = True, max_web_docs: int = 8) -> PRRecord:
    from pathlib import Path

    from . import pr_source, repo as repo_mod
    from .config import PROJECT_ROOT

    model = model or providers.DEFAULT_CHAT_MODEL[provider]
    embed_provider = embed_provider or provider  # embeddings default to the gen provider
    ref = pr_source.parse_pr_ref(reference, repo=repo)
    bundle = pr_source.fetch_pr_bundle(ref, token=cfg.github_token,
                                       include_logs=False, include_commit_tests=True)

    full_evidence = "\n".join([bundle.title, bundle.body]
        + [li.title + "\n" + li.body for li in bundle.linked_issues] + [bundle.diff])
    kw = sorted({h.keyword for h in keyword_filter.scan(full_evidence)})
    modified = [f["filename"] for f in bundle.files]
    # test files THIS PR's diff touches (precise) — not the noisy per-commit set.
    tests = sorted(f for f in modified if is_test_file(f))

    rec = PRRecord(reference=ref.html_url, repo=f"{ref.owner}/{ref.repo}", pr_number=ref.number,
                   linked_issues=[li.number for li in bundle.linked_issues],
                   buggy_commit=bundle.buggy_commit, fixed_commit=bundle.fixed_commit,
                   modified_files=modified, test_files=tests, keyword_signal=kw)

    if require_merged and not bundle.merged:
        rec.note = f"PR is {bundle.state or 'open'}, not merged — skipped."
        return rec

    dest = Path(work_dir) if work_dir else PROJECT_ROOT / "workspace" / f"{ref.repo}_{ref.number}"
    cloned = False
    repo_files: list = []
    try:
        state = repo_mod.clone_and_reset(f"{ref.owner}/{ref.repo}", bundle.buggy_commit,
                                         dest, token=cfg.github_token)
        repo_files = state.files
        cloned = True
    except Exception as e:  # noqa: BLE001 — still answer from PR docs
        rec.note = f"clone failed ({type(e).__name__}: {e}); PR docs only"

    # Directly referenced pages: the PR, its issues and the README may point at
    # install guides or API docs that the repository itself never states.
    web_docs: list = []
    if fetch_web:
        from . import webdocs
        sources = [("pr_body", f"{bundle.title}\n{bundle.body}")]
        sources += [(f"issue#{li.number}", f"{li.title}\n{li.body}\n" + "\n".join(li.comments[:6]))
                    for li in bundle.linked_issues]
        sources += [("discussion", "\n".join(bundle.issue_comments[:12] + bundle.review_bodies[:12]))]
        sources += [(f"readme:{rf.path}", rf.text) for rf in repo_files if rf.role == "readme"]
        # links the PR body inherited from the repository's template are not
        # evidence about this bug — they are on every PR the project ever had
        boilerplate = webdocs.template_urls(repo_files)
        try:
            web_docs = webdocs.fetch_referenced(sources, exclude=boilerplate,
                                                max_docs=max_web_docs)
        except Exception as e:  # noqa: BLE001 — the corpus is fine without them
            rec.note = (rec.note + f"; web fetch failed ({type(e).__name__})").strip("; ")
        rec.web_documents = [{"url": d.url, "origin": d.origin, "title": d.title,
                              "n_chars": len(d.text)} for d in web_docs]

    try:
        chunks = build_documents(bundle, repo_files, web_docs)
        cdir = Path(chroma_dir) if chroma_dir else PROJECT_ROOT / "workspace" / "chroma"
        store = ChromaStore(cdir, f"{ref.repo}_{ref.number}", provider=embed_provider,
                            embed_model=embed_model, host=host)
        rec.n_chunks = store.add(chunks)

        in_tok = out_tok = cached_tok = n_calls = 0
        cost = billed = 0.0
        peak_calls = 0
        # Every question runs for every candidate: there is no classification
        # gate any more, so nothing is skipped on a model's say-so.
        for q in QUESTIONS:
            if only and q.id not in only:
                continue
            schema, extra_note = None, ""
            a, usage = answer(q, store, bundle, repo_files, provider=provider, model=model,
                              host=host, schema=schema, extra_note=extra_note)
            rec.answers[q.id] = {"answer": a.data, "grounded_on": a.grounded_on, "error": a.error}
            if usage:
                in_tok += usage.input_tokens; out_tok += usage.output_tokens
                cached_tok += usage.cache_read_tokens
                cost += usage.cost_usd            # peak rates: an upper bound
                billed += usage.cost_usd_billed   # the window the call landed in
                peak_calls += 1 if usage.peak else 0
                n_calls += 1
        rec.usage = {"model": model, "llm_calls": n_calls,
                     "questions_answered": len(rec.answers),
                     "input_tokens": in_tok + cached_tok,     # every prompt token
                     "uncached_input_tokens": in_tok,
                     "cached_input_tokens": cached_tok,
                     "output_tokens": out_tok, "peak_calls": peak_calls,
                     "cost_usd": round(cost, 6),                # peak, cache-miss
                     "cost_usd_billed_estimate": round(billed, 6)}
    finally:
        # Answers are extracted from the index — drop the (large) clone to keep
        # workspace/ bounded. Runs even if indexing/answering raised.
        if cleanup_clone and cloned:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            rec.note = (rec.note + "; " if rec.note else "") + "clone removed"
    return rec


def mine_repo(cfg, repo: str, *, limit: int | None = 10, provider: str = "ollama",
              model: str | None = None, embed_provider: str | None = None,
              embed_model: str | None = None, host: str = providers.OLLAMA_HOST,
              work_dir=None, chroma_dir=None, only: list[str] | None = None,
              cleanup_clone: bool = True,
              fetch_web: bool = True, max_web_docs: int = 8,
              since=None, until=None, require_tests: bool = False,
              require_source_change: bool = True,
              scan_cap: int | None = 1000, progress=None) -> dict:
    """Input a repo name, output structured candidate bug-fix records."""
    from .crawler import crawl_closed_prs

    crawl_stats: dict = {}
    prs = crawl_closed_prs(repo, cfg.github_token, only_merged=True,
                           keyword_prefilter=True, require_test_commits=require_tests,
                           require_source_change=require_source_change,
                           since=since, until=until, limit=limit, scan_cap=scan_cap,
                           stats=crawl_stats)
    if progress:
        stopped = crawl_stats.get("scan_stopped")
        note = {"scan_cap": " [SCAN CAP HIT — window not fully covered]",
                "limit": " [limit reached]"}.get(stopped, "")
        progress(f"{repo}: {len(prs)} candidate(s) survived pre-RAG filtering "
                 f"of {crawl_stats.get('scanned', 0)} closed PR(s) scanned{note}")
    candidates = []
    for i, p in enumerate(prs, 1):
        if progress:
            progress(f"[{i}/{len(prs)}] mining #{p.number}: {p.title}")
        rec = mine_pr(cfg, f"{repo}#{p.number}", provider=provider, model=model,
                      embed_provider=embed_provider, embed_model=embed_model, host=host,
                      work_dir=work_dir, chroma_dir=chroma_dir, only=only,
                      cleanup_clone=cleanup_clone,
                      fetch_web=fetch_web, max_web_docs=max_web_docs)
        candidates.append(rec.to_dict())
    return {"repo": repo, "n_candidates": len(candidates),
            "crawl_stats": crawl_stats, "rag_stats": rag_stats(candidates),
            "usage_totals": usage_totals(candidates),
            "provider": provider, "model": model or providers.DEFAULT_CHAT_MODEL[provider],
            "candidates": candidates}

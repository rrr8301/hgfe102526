"""The pipeline: PR link -> crawl -> clone -> view -> RAG -> compact context.

One confirmed numerical bug at a time. Nothing here decides whether the PR is a
numerical bug; the annotated category arrives as an argument and conditions the
retrieval (DP1/DP3).

The GitHub fetch, the clone, the chunker, the index and the provider clients are
imported from `numbugminer`, which this package does not modify.
"""
from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from numbugminer import mine as _nbm_mine         # build_documents, is_test_file
from numbugminer import pr_source, providers
from numbugminer import repo as repo_mod
from numbugminer.chroma_store import ChromaStore
from numbugminer.config import PROJECT_ROOT

from . import audit as audit_mod
from . import completeness as comp
from . import era as era_mod
from . import questions as Q
from . import views as V
from .reproducers import (fetch_gh_issue_chunks, gh_referenced_issues,
                          reproducer_chunks)
from .context import NumericalContext

SYSTEM = (
    "You extract reproduction evidence for a CONFIRMED numerical bug. The bug's "
    "category is given; do not re-classify it and do not argue about whether it "
    "is numerical. Answer only from the supplied passages. Every value you give "
    "must appear in, or follow directly from, a passage — when the evidence is "
    "silent, use null. A guessed version number, tolerance or expected value is "
    "worse than a null, because the reproduction will be judged against it."
)


@dataclass
class MineResult:
    context: NumericalContext
    raw_chars: int = 0
    seconds: float = 0.0
    note: str = ""
    chunks_indexed: dict = field(default_factory=dict)
    completeness: dict = field(default_factory=dict)
    audit: dict = field(default_factory=dict)


def _tag(meta: dict) -> str:
    tag = f"{meta.get('source_type')}:{meta.get('file_path') or ''}"
    if meta.get("line_start"):
        tag += f":{meta['line_start']}-{meta['line_end']}"
    return tag


def _ask(question: Q.Question, store: ChromaStore, *, category: str, provider: str,
         model: str, host: str, view: V.View, extra: str = "") -> tuple:
    """One question against one view. Returns (data, refs, usage, error)."""
    hits = store.query_balanced(question.retrieval_query(category),
                                k=question.k,
                                source_types=V.allowed_sources(view, question.sources))
    # DP5: a chunk from a withheld source must not reach the prompt even if the
    # collection were built wrongly — belt as well as braces.
    hits = [h for h in hits
            if V.provenance_ok(view, (h["metadata"] or {}).get("source_type", ""))]
    passages, refs = [], []
    for h in hits:
        refs.append(_tag(h["metadata"]))
        passages.append(f"[{refs[-1]} sim={h['score']}]\n{h['text']}")

    user = (f"QUESTION: {Q.render(question, category)}\n\n"
            f"CONTEXT PASSAGES:\n" + "\n\n---\n\n".join(passages)
            + (f"\n\nADDITIONAL REQUEST:\n{extra}" if extra else "")
            + "\n\nAnswer as a JSON object per the schema, grounded only in the "
              "passages above. Use null where they are silent.")
    try:
        res = providers.chat(provider, model, SYSTEM, user, schema=question.schema,
                             host=host, max_tokens=1200)
        return providers.extract_json(res.text), refs, res.usage, ""
    except Exception as e:                       # noqa: BLE001 — one question
        return {}, refs, None, f"{type(e).__name__}: {e}"


#: `numpy>=1.26`, `pandas 2.1`, `scipy` — a package name and whatever follows it
_DEP_TEXT = re.compile(r"^([A-Za-z0-9._+-]+)\s*(.*)$")


def _as_deps(value) -> list:
    """Dependencies in the documented shape, whatever the model actually returned.

    The schema asks for objects, and the providers usually honour it — but not
    reliably, and the two shapes that come back instead (a list of requirement
    strings, or one comma-separated string) used to raise `AttributeError: 'str'
    object has no attribute 'get'` from the provenance loop below. That crash
    landed AFTER the crawl, the clone and all three paid questions, destroying a
    run over a formatting difference.

    A bare string states a package and possibly a version, but says nothing about
    where either came from, so it is recorded as `documented: False`: era.py then
    treats the constraint as inferred rather than evidenced, which is the
    conservative reading. `shape_recovered` marks the entry so the provenance
    record does not imply the model answered in the shape it was asked for.
    """
    if isinstance(value, str):
        value = [part for part in re.split(r"[,;\n]", value) if part.strip()]
    out = []
    for item in value or []:
        if isinstance(item, dict):
            if str(item.get("name") or "").strip():
                out.append(item)
            continue
        if not isinstance(item, str) or not item.strip():
            continue
        m = _DEP_TEXT.match(item.strip())
        name, rest = (m.group(1), m.group(2).strip()) if m else (item.strip(), "")
        out.append({"name": name, "constraint": rest or None, "documented": False,
                    "numerically_relevant": False, "source": "",
                    "shape_recovered": True})
    return out


def _usage_add(total: dict, usage) -> None:
    if not usage:
        return
    for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cost_usd"):
        total[k] = total.get(k, 0) + (getattr(usage, k, 0) or 0)
    total["llm_calls"] = total.get("llm_calls", 0) + 1


def mine_pr(cfg, reference: str, *, category: str, task: str = "no_diff",
            provider: str = "openai", model: str = "", host: str = "",
            embed_provider: str = "ollama", embed_model: str = "",
            work_dir=None, chroma_dir=None, cleanup_clone: bool = True,
            keep_chunks: bool = False, repair_calls: int = 1,
            audit_isolation: bool = True) -> MineResult:
    """Mine one confirmed numerical bug into a compact reproduction context.

    `task` names the evidence view. Under the default `no_diff` every question
    is answered from the report and the repository, and no diff is indexed at
    all. Under `brtg_*` the environment question is instead answered in the HBEC
    view, which may see the fix; the two never share a collection, so what the
    BRTG view cannot read cannot reach its answers (DP5).
    """
    started = time.monotonic()
    model = model or providers.DEFAULT_CHAT_MODEL.get(provider, "")
    # "" from the CLI means "the provider's default endpoint" — passing it on
    # reaches the embedder as a schemeless URL and fails at the first query
    host = host or providers.OLLAMA_HOST
    brtg_view = V.VIEWS.get(task) or V.NO_DIFF
    ref = pr_source.parse_pr_ref(reference)

    # ---- crawl -------------------------------------------------------------
    bundle = pr_source.fetch_pr_bundle(ref, token=cfg.github_token,
                                       include_logs=False, include_commit_tests=True)
    ctx = NumericalContext(
        repo=f"{ref.owner}/{ref.repo}", pr_number=ref.number, reference=ref.html_url,
        buggy_commit=bundle.buggy_commit, fixed_commit=bundle.fixed_commit,
        category=(category or "").strip().lower(), view=brtg_view.name,
        withheld=list(brtg_view.withheld))
    ctx.numerical_failure["category"] = ctx.category

    # ---- clone at the buggy commit ----------------------------------------
    dest = Path(work_dir) if work_dir else PROJECT_ROOT / "workspace" / f"nsm_{ref.repo}_{ref.number}"
    repo_files, note, cloned = [], "", False
    try:
        state = repo_mod.clone_and_reset(f"{ref.owner}/{ref.repo}", bundle.buggy_commit,
                                         dest, token=cfg.github_token)
        repo_files, cloned = state.files, True
    except Exception as e:                       # noqa: BLE001 — PR docs still answer
        note = f"clone failed ({type(e).__name__}: {e}); PR documents only"

    # `PRBundle` carries no date AT ALL -- it has `merged`, a bool, not
    # `merged_at` -- so all three lookups below returned their getattr default
    # and every mined context was written with an empty pr_date. Nothing raised,
    # because a default cannot fail. `to_prompt()` then renders "PR DATE: " and
    # the planner reads the blank as "no era is stated": scipy#19933 and
    # numpy#25592 both wrote "PR DATE is null, so ... no rebuild planned" on
    # every one of seven iterations and never built the package. The clone above
    # is already at the buggy commit, so git answers it locally with no API
    # call; the bundle lookups stay as a fallback should the field ever exist.
    pr_date = ""
    if cloned:
        try:
            shown = subprocess.run(
                ["git", "show", "-s", "--format=%cI", state.commit or "HEAD"],
                cwd=state.worktree, capture_output=True, text=True,
                timeout=60, check=False)
            pr_date = (shown.stdout or "").strip()
        except Exception:                # noqa: BLE001 — a date is not worth the run
            pr_date = ""
    for attr in ("merged_at", "created_at", "buggy_commit_date"):
        pr_date = pr_date or str(getattr(bundle, attr, "") or "")
    ctx.pr_date = pr_date[:10]

    try:
        # ---- build the two views ------------------------------------------
        chunks = _nbm_mine.build_documents(bundle, repo_files, [])
        # numbugminer splits every document at 1200 chars, which cuts a pasted
        # reproducer in half: sympy#26822's 7x7 matrix spans four chunks of
        # issue gh-26821, and the mined context said so in its own words
        # ("matrix not fully shown in the provided passages"). The fenced code
        # blocks are therefore ALSO indexed whole, beside the split prose. Same
        # sources, same view filter — only the cut is different.
        chunks = chunks + reproducer_chunks(bundle)
        # Issues the PR references as `gh-NNNN` rather than `#NNNN`. numbugminer's
        # closing-keyword pattern only matches the `#` form, so for SciPy, NumPy
        # and SymPy — which all write `fixes gh-26821` — the referenced issue was
        # never fetched, and its reproducer could not be retrieved because it was
        # never in the corpus. `issue` is an allowed source under every view, so
        # this admits nothing new in kind, only what was being dropped.
        extra_issues = fetch_gh_issue_chunks(bundle, cfg.github_token)
        if extra_issues:
            # `provenance` is where this belongs: it records WHERE the evidence
            # came from, and an instance whose trigger was recovered this way
            # should say so.
            ctx.provenance.append({
                "source": "gh_referenced_issues",
                "numbers": gh_referenced_issues(bundle),
                "chunks": len(extra_issues),
                "why": "referenced as gh-NNNN, which the closing-keyword "
                       "pattern does not match"})
        chunks = chunks + extra_issues
        raw_chars = sum(len(c.text) for c in chunks)
        cdir = Path(chroma_dir) if chroma_dir else PROJECT_ROOT / "workspace" / "nsm_chroma"
        stores, indexed = {}, {}
        # only the views a question is actually answered in. Under `no_diff`
        # that is one collection, and the diff is never embedded at all — not
        # merely never retrieved.
        for view in {V.view_for(q.id, brtg_view) for q in Q.QUESTIONS}:
            kept = V.filter_chunks(chunks, view)
            store = ChromaStore(cdir, f"nsm_{ref.repo}_{ref.number}_{view.name}",
                                provider=embed_provider, embed_model=embed_model,
                                host=host)
            indexed[view.name] = store.add(kept)
            stores[view.name] = store

        # ---- the three questions ------------------------------------------
        usage: dict = {}
        for question in Q.QUESTIONS:
            view = V.view_for(question.id, brtg_view)
            data, refs, u, err = _ask(question, stores[view.name], category=ctx.category,
                                      provider=provider, model=model, host=host, view=view)
            _usage_add(usage, u)
            ctx.retrieval[question.id] = {"view": view.name, "refs": refs,
                                          "n_passages": len(refs)}
            if err:
                ctx.errors[question.id] = err
                continue
            try:
                _absorb(ctx, question.id, data, refs)
            except Exception as e:                   # noqa: BLE001
                # The crawl, the clone and this question are already paid for.
                # Record the shape problem, keep the rest of the context, and let
                # the completeness report show the resulting hole.
                ctx.errors[question.id] = (f"could not absorb the answer: "
                                           f"{type(e).__name__}: {e}")
        ctx.usage = usage

        # ---- completeness repair, before anything downstream reads this ----
        # A required field left empty by the first pass is a known hole, and a
        # strategy written over a hole is the expensive kind of failure. One
        # targeted re-ask is far cheaper than the full-context fallback it would
        # otherwise take to close it.
        if repair_calls > 0:
            from .escalate import Budget, climb
            ctx, budget = climb(ctx, task=comp.BRTG,
                                budget=Budget(max_calls=repair_calls,
                                              max_rung="widened"),
                                provider=provider, model=model, host=host,
                                embed_provider=embed_provider,
                                embed_model=embed_model, chroma_dir=chroma_dir)

        # ---- era reconstruction (DP6) --------------------------------------
        when = era_mod.parse_date(ctx.pr_date)
        constraints = era_mod.era_constraints(ctx.environment.get("dependencies"), when)
        ctx.environment["era_constraints"] = era_mod.summarise(constraints, when)
        ctx.environment["dependency_constraints"] = constraints

        # ---- completeness and isolation, recorded with the context ---------
        report = comp.both_tasks(ctx)
        ctx.retrieval["completeness"] = report
        withheld_text = allowed_text = ""
        if audit_isolation:
            withheld_text = "\n".join(
                c.text for c in chunks
                if (c.metadata or {}).get("source_type") in brtg_view.withheld)
            # what the view WAS allowed to read: content present here cannot be
            # evidence of a leak, however much it also appears in the test
            allowed_text = "\n".join(
                c.text for c in chunks
                if brtg_view.keeps((c.metadata or {}).get("source_type", "")))
        isolation = audit_mod.audit(ctx, withheld_text=withheld_text,
                                    allowed_text=allowed_text).as_dict()
        ctx.retrieval["isolation"] = isolation

        result = MineResult(context=ctx, raw_chars=raw_chars,
                            seconds=round(time.monotonic() - started, 1),
                            note=note, chunks_indexed=indexed,
                            completeness=report, audit=isolation)
        if keep_chunks:
            result.chunks_indexed["total_chunks"] = len(chunks)
        return result
    finally:
        if cleanup_clone and cloned:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


def _absorb(ctx: NumericalContext, qid: str, data: dict, refs: list) -> None:
    """Fold one answer into the structured context, recording provenance."""
    where = refs[0] if refs else ""
    src = where.split(":", 1)[0] if where else ""

    if qid == "environment":
        env = ctx.environment
        for key in ("language", "language_version", "numerical_backend"):
            if data.get(key):
                env[key] = data[key]
                ctx.record(f"environment.{key}", data[key], src, where)
        for key in ("installation_commands", "build_commands", "test_commands",
                    "system_packages"):
            if data.get(key):
                env[key] = list(data[key])
                ctx.record(f"environment.{key}", data[key], src, where)
        deps = _as_deps(data.get("dependencies"))
        if deps:
            env["dependencies"] = deps
            for dep in deps:
                ctx.record("environment.dependency", dep.get("name"),
                           dep.get("source") or src, where)
    elif qid == "failure":
        fail = ctx.numerical_failure
        for key in ("affected_api", "observed_behavior", "expected_behavior",
                    "manifestation"):
            if data.get(key):
                fail[key] = data[key]
                ctx.record(f"numerical_failure.{key}", data[key], src, where)
        for key in ("trigger_conditions", "oracle_clues", "input_output_examples"):
            if data.get(key):
                fail[key] = list(data[key])
                ctx.record(f"numerical_failure.{key}", len(data[key]), src, where)
    elif qid == "characteristics":
        chars = ctx.numerical_characteristics
        for key in ("dtype", "precision", "magnitude", "tolerance"):
            if data.get(key):
                chars[key] = data[key]
                ctx.record(f"numerical_characteristics.{key}", data[key], src, where)
        for key in ("category_specific_evidence", "reproduction_risks"):
            if data.get(key):
                chars[key] = list(data[key])
                ctx.record(f"numerical_characteristics.{key}", len(data[key]), src, where)

"""Adaptive retrieval as a budgeted ladder (DP7).

The ablation it answers: full-context prompting reproduced more bugs than
compact RAG, and cost far more tokens. Neither extreme is the right default —
paying full-context prices on the ~70% of instances that compact context already
reproduces is waste, and capping every instance at a short summary leaves the
rest unreproducible.

So the context climbs only when something failed, one rung at a time:

    rung 0  compact context                       (the default; ~1-2k chars)
    rung 1  targeted re-ask        +1 call        the diagnosed question, expanded query
    rung 2  widened retrieval      +1 call        more passages, neighbouring sources
    rung 3  full context for THIS instance        the whole allowed evidence, uncompressed

Rung 3 is the ablation's winner used as a fallback rather than a policy. Every
rung records what it cost, so "tokens per successful reproduction" can be
reported per rung and the trade-off measured instead of argued.

Isolation is preserved at every rung: the ladder never widens past the view's
allowed sources, so a BRTG instance cannot climb its way to the developer's test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from numbugminer import providers
from numbugminer.chroma_store import ChromaStore
from numbugminer.config import PROJECT_ROOT

from .store import ReadOnlyStore

from . import completeness as comp
from . import questions as Q
from . import views as V
from .context import NumericalContext
from .expand import Request, diagnose

RUNGS = ("compact", "targeted", "widened", "full_context")


@dataclass
class Budget:
    """What an instance may spend climbing. Counted in LLM calls, not tokens,
    because a call is what the harness can refuse to make."""
    max_calls: int = 3
    max_rung: str = "full_context"
    spent: int = 0
    log: list = field(default_factory=list)

    def can(self, rung: str) -> bool:
        return (self.spent < self.max_calls
                and RUNGS.index(rung) <= RUNGS.index(self.max_rung))

    def charge(self, rung: str, usage, note: str = "") -> None:
        self.spent += 1
        self.log.append({
            "rung": rung, "note": note,
            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cost_usd": getattr(usage, "cost_usd", 0.0) or 0.0,
        })

    def as_dict(self) -> dict:
        return {"max_calls": self.max_calls, "max_rung": self.max_rung,
                "spent": self.spent, "log": self.log}


def _store(ctx: NumericalContext, view: V.View, *, embed_provider: str,
           embed_model: str, host: str = "", chroma_dir=None) -> ChromaStore:
    host = host or providers.OLLAMA_HOST
    cdir = Path(chroma_dir) if chroma_dir else PROJECT_ROOT / "workspace" / "nsm_chroma"
    # ReadOnlyStore, not ChromaStore: the ladder READS an index the miner built,
    # and ChromaStore's constructor would delete it before the first query
    return ReadOnlyStore(cdir, f"nsm_{ctx.repo.split('/')[-1]}_{ctx.pr_number}_{view.name}",
                         provider=embed_provider, embed_model=embed_model, host=host)


def _passages(store: ChromaStore, question: Q.Question, view: V.View, *,
              category: str, extra_terms: str = "", k: int | None = None,
              widen: bool = False) -> tuple:
    """Retrieved passages and their tags, filtered to the view's allowed sources."""
    sources = V.allowed_sources(view, question.sources)
    if widen:
        # neighbouring sources the question does not normally read — still only
        # ones this view is allowed to hold
        sources = tuple(s for s in V.PR_INFO + V.REPO + V.SOURCE_DIFF
                        if view.keeps(s))
    hits = store.query_balanced(" ".join([question.retrieval_query(category), extra_terms]),
                                k=k or question.k, source_types=sources)
    hits = [h for h in hits
            if V.provenance_ok(view, (h["metadata"] or {}).get("source_type", ""))]
    tags, texts = [], []
    for h in hits:
        m = h["metadata"]
        tag = f"{m.get('source_type')}:{m.get('file_path') or ''}"
        tags.append(tag)
        texts.append(f"[{tag} sim={h['score']}]\n{h['text']}")
    return texts, tags


def _ask(question: Q.Question, *, ctx: NumericalContext, passages: list,
         instruction: str, provider: str, model: str, host: str) -> tuple:
    from .miner import SYSTEM
    user = (f"QUESTION: {Q.render(question, ctx.category)}\n\n"
            f"{instruction}\n\n"
            f"ALREADY KNOWN (extend, do not repeat):\n{ctx.to_prompt()}\n\n"
            f"CONTEXT PASSAGES:\n" + "\n\n---\n\n".join(passages)
            + "\n\nAnswer as a JSON object per the schema, grounded only in the "
              "passages. Null where they are silent.")
    try:
        res = providers.chat(provider, model, SYSTEM, user, schema=question.schema,
                             host=host, max_tokens=1400)
        return providers.extract_json(res.text), res.usage, ""
    except Exception as e:                       # noqa: BLE001
        return {}, None, f"{type(e).__name__}: {e}"


def climb(ctx: NumericalContext, *, result: dict | None = None,
          task: str = comp.BRTG, budget: Budget | None = None,
          provider: str = "openai", model: str = "", host: str = "",
          embed_provider: str = "ollama", embed_model: str = "",
          chroma_dir=None) -> tuple:
    """Climb the ladder until the gap closes or the budget runs out.

    `result` is a finished `numagent` result when the climb follows a failed
    reproduction; without it the climb is driven by completeness alone, which is
    what the miner does before any strategy is written.

    Returns `(ctx, budget)` — the context carries the record of each rung in
    `ctx.expansions`, so a later analysis can attribute tokens to rungs.
    """
    from .miner import _absorb, _usage_add

    budget = budget or Budget()
    view = V.VIEWS.get(ctx.view) or V.NO_DIFF

    # --- what is wrong: a reproduction failure outranks a completeness gap --
    request: Request | None = diagnose(result) if result else None
    report = comp.assess(ctx, task)
    gaps = comp.gaps_by_question(report)

    plan: list = []
    if request:
        plan.append(("targeted", request.question_id, request.ask, request.extra_terms))
    for qid, paths in gaps.items():
        if not request or qid != request.question_id:
            plan.append(("targeted", qid, comp.gap_instruction(paths, report), ""))
    if not plan:
        return ctx, budget                       # complete, and nothing failed

    for rung, qid, instruction, terms in plan:
        if not budget.can(rung):
            break
        question = next(q for q in Q.QUESTIONS if q.id == qid)
        q_view = V.view_for(qid, view)
        store = _store(ctx, q_view, embed_provider=embed_provider,
                       embed_model=embed_model, host=host, chroma_dir=chroma_dir)
        texts, tags = _passages(store, question, q_view, category=ctx.category,
                                extra_terms=terms)
        data, usage, err = _ask(question, ctx=ctx, passages=texts,
                                instruction=instruction, provider=provider,
                                model=model, host=host)
        budget.charge("targeted", usage, f"{qid}: {instruction[:60]}")
        _usage_add(ctx.usage, usage)
        if data:
            _absorb(ctx, qid, data, tags)
        ctx.expansions.append({"rung": "targeted", "question": qid, "error": err,
                               "n_passages": len(texts),
                               "fields_added": sorted(data) if data else []})

    # --- rung 2/3: still incomplete, so widen, then stop compressing --------
    report = comp.assess(ctx, task)
    if report["required_missing"] and budget.can("widened"):
        qid = comp.gaps_by_question(report).popitem()[0] if comp.gaps_by_question(report) else None
        if qid:
            question = next(q for q in Q.QUESTIONS if q.id == qid)
            q_view = V.view_for(qid, view)
            store = _store(ctx, q_view, embed_provider=embed_provider,
                           embed_model=embed_model, host=host, chroma_dir=chroma_dir)
            texts, tags = _passages(store, question, q_view, category=ctx.category,
                                    k=question.k * 3, widen=True)
            data, usage, err = _ask(
                question, ctx=ctx, passages=texts,
                instruction=("A narrower search did not find these fields. Here is a "
                             "wider slice of the evidence — including sources this "
                             "question does not normally read."),
                provider=provider, model=model, host=host)
            budget.charge("widened", usage, qid)
            _usage_add(ctx.usage, usage)
            if data:
                _absorb(ctx, qid, data, tags)
            ctx.expansions.append({"rung": "widened", "question": qid, "error": err,
                                   "n_passages": len(texts),
                                   "fields_added": sorted(data) if data else []})

    report = comp.assess(ctx, task)
    ctx.retrieval["completeness"] = report
    ctx.retrieval["budget"] = budget.as_dict()
    return ctx, budget


def full_context_block(bundle, repo_files: list, view: V.View,
                       max_chars: int = 120_000) -> str:
    """Rung 3: the whole of what this view may see, uncompressed.

    This is the ablation's full-context arm, reachable per instance. It is built
    from the same allow-list as the index, so even the fallback cannot show a
    BRTG instance the developer's test.
    """
    parts = [f"# PULL REQUEST {bundle.title}\n{bundle.body}"]
    for li in bundle.linked_issues:
        parts.append(f"# ISSUE #{li.number} {li.title}\n{li.body}\n"
                     + "\n".join(li.comments[:8]))
    if view.keeps("discussion"):
        parts.append("# DISCUSSION\n" + "\n".join(bundle.issue_comments[:12]))
    if view.keeps("readme"):
        parts += [f"# {rf.path}\n{rf.text}" for rf in repo_files
                  if getattr(rf, "role", "") in ("readme", "config")]
    if view.keeps("diff"):
        parts.append("# SOURCE DIFF\n" + (bundle.diff or "")[:40_000])
    text = "\n\n".join(parts)
    return text[:max_chars]

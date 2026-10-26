"""Adaptive evidence expansion (DP7).

The compact context is the default, not a ceiling. When a reproduction fails in
a way that points at missing evidence, the agent asks for more — and only then,
and only about the thing that failed:

    compact context -> strategy -> PEVR
                                     |  fails
                      failure diagnosis -> targeted request -> expand() -> strategy'

`diagnose()` turns a finished `numagent` result into a request. `expand()` answers
that request against the SAME view the context was mined in, so an expansion can
never admit evidence the task was meant not to see (DP5).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from numbugminer import providers
from numbugminer.config import PROJECT_ROOT

from . import questions as Q
from . import views as V
from .context import NumericalContext
from .miner import SYSTEM, _tag
from .store import ReadOnlyStore

#: what a failure says about the evidence that is missing. The key is read off
#: the reproduction result; the value is the question to re-ask and the terms to
#: add to the retrieval query.
DIAGNOSES = {
    "buggy_passes": (
        "environment",
        "The buggy revision PASSES the reproducing test, so the defect is not "
        "present as built. What in the historical environment — dependency "
        "versions, build flags, backend, dtype defaults — determines whether this "
        "behaviour appears at all?",
        "version pin build flag backend architecture platform dtype default "
        "compiler optimisation fast-math"),
    "both_fail": (
        "environment",
        "The test FAILS on both revisions, so the environment may be wrong rather "
        "than the defect absent. What does this revision need in order to build "
        "and run its tests correctly?",
        "build failure compile error missing dependency version conflict test "
        "collection error"),
    "no_numeric_signal": (
        "characteristics",
        "The test transitions but no numerical difference was observed. Which "
        "concrete quantity differs between the revisions, at what dtype, "
        "magnitude and tolerance, and how would one observe it directly?",
        "observable quantity assertion tolerance dtype magnitude reference value"),
    "values_not_reproduced": (
        "failure",
        "The reported values were not reproduced. What exactly did the reporter "
        "run, and what exact values did they report — inputs, wrong output, "
        "expected output?",
        "reproduction script exact value output paste traceback repro steps"),
    "test_not_found": (
        "environment",
        "The reproducing test could not be selected. How are this project's tests "
        "addressed and run at this revision?",
        "pytest addressing module path test selection runner command"),
}


@dataclass
class Request:
    kind: str
    question_id: str
    ask: str
    extra_terms: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "question": self.question_id, "ask": self.ask}


def diagnose(result: dict) -> Request | None:
    """Read a `numagent` result and decide what, if anything, to ask for.

    Returns None when the run reproduced the bug, or when it failed for a reason
    more evidence cannot fix (a container that never started, a skipped test).
    """
    status = (result or {}).get("status", "")
    transition = (result or {}).get("transition", "")
    numeric = (result or {}).get("numeric_verdict")
    examples = (result or {}).get("examples_verdict")
    cause = ((result or {}).get("setup_cause") or {}).get("cause", "")

    if status in ("REPRODUCED", "F2P_REPRODUCED"):
        return None
    if cause in ("tests_skipped", "nothing_ran"):
        return None                                  # evidence will not help
    if transition in ("pass->pass", "pass->error"):
        return _make("buggy_passes")
    if transition == "fail->fail":
        return _make("both_fail")
    if cause in ("test_collection_error", "test_error") or transition.endswith("no_tests"):
        return _make("test_not_found")
    if result.get("fail_to_pass") and numeric in ("absent", "inconclusive", None):
        return _make("no_numeric_signal")
    if result.get("fail_to_pass") and examples in ("not_reproduced", "not_observed"):
        return _make("values_not_reproduced")
    return None


def _make(kind: str) -> Request:
    qid, ask, terms = DIAGNOSES[kind]
    return Request(kind=kind, question_id=qid, ask=ask, extra_terms=terms)


def expand(ctx: NumericalContext, request: Request, *, provider: str, model: str,
           host: str = "", embed_provider: str = "ollama", embed_model: str = "",
           chroma_dir=None) -> NumericalContext:
    """Answer one targeted request and fold the result into the context.

    The collection is the one the context was mined in, so the expansion inherits
    its isolation: a BRTG context cannot acquire the developer's test by asking a
    second time.
    """
    question = next(q for q in Q.QUESTIONS if q.id == request.question_id)
    view = V.view_for(question.id, V.VIEWS.get(ctx.view) or V.NO_DIFF)
    cdir = Path(chroma_dir) if chroma_dir else PROJECT_ROOT / "workspace" / "nsm_chroma"
    store = ReadOnlyStore(cdir, f"nsm_{ctx.repo.split('/')[-1]}_{ctx.pr_number}_{view.name}",
                          provider=embed_provider, embed_model=embed_model,
                          host=host or providers.OLLAMA_HOST)

    query = " ".join([question.retrieval_query(ctx.category), request.extra_terms])
    hits = [h for h in store.query_balanced(
                query, k=question.k + 4,
                source_types=V.allowed_sources(view, question.sources))
            if V.provenance_ok(view, (h["metadata"] or {}).get("source_type", ""))]
    passages = [f"[{_tag(h['metadata'])} sim={h['score']}]\n{h['text']}" for h in hits]

    user = (f"QUESTION: {Q.render(question, ctx.category)}\n\n"
            f"A REPRODUCTION ATTEMPT FAILED. TARGETED REQUEST:\n{request.ask}\n\n"
            f"WHAT IS ALREADY KNOWN (do not repeat, extend):\n{ctx.to_prompt()}\n\n"
            f"CONTEXT PASSAGES:\n" + "\n\n---\n\n".join(passages)
            + "\n\nAnswer as a JSON object per the schema. Add only what the "
              "passages support; keep null where they are silent.")
    try:
        res = providers.chat(provider, model, SYSTEM, user, schema=question.schema,
                             host=host, max_tokens=1200)
        data = providers.extract_json(res.text)
        usage = res.usage
        error = ""
    except Exception as e:                           # noqa: BLE001
        data, usage, error = {}, None, f"{type(e).__name__}: {e}"

    from .miner import _absorb, _usage_add
    if data:
        _absorb(ctx, question.id, data, [_tag(h["metadata"]) for h in hits])
    _usage_add(ctx.usage, usage)
    ctx.expansions.append({**request.as_dict(), "n_passages": len(passages),
                           "error": error,
                           "fields_added": sorted(data) if data else []})
    return ctx

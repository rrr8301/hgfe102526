from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from NumericSpecificMiner import escalate as nsm_escalate
from NumericSpecificMiner import questions as nsm_q
from NumericSpecificMiner import views as nsm_views

_ROUTE = (
    ("environment", ("version", "dependency", "install", "build", "python",
                     "backend", "blas", "lapack", "compiler", "era", "package")),
    ("characteristics", ("dtype", "precision", "tolerance", "magnitude", "ulp",
                         "nan", "inf", "convergence", "iteration", "residual")),
    ("failure", ("expected", "observed", "value", "trigger", "input", "oracle",
                 "reported", "assert", "behaviour", "behavior")),
)


def route(information_need: str) -> str:
    low = (information_need or "").lower()
    best, score = "failure", 0
    for qid, terms in _ROUTE:
        hits = sum(1 for t in terms if t in low)
        if hits > score:
            best, score = qid, hits
    return best


@dataclass
class EvidenceBudget:
    max_calls: int = 2
    spent: int = 0
    answers: dict = field(default_factory=dict)
    log: list = field(default_factory=list)

    def exhausted(self) -> bool:
        return self.spent >= self.max_calls

    def seen(self, need: str, cutoff: float = 0.82) -> str:
        for asked, answer in self.answers.items():
            if difflib.SequenceMatcher(None, asked.lower(), need.lower()).ratio() >= cutoff:
                return answer
        return ""

    def as_dict(self) -> dict:
        return {"max_calls": self.max_calls, "spent": self.spent,
                "needs": list(self.answers), "log": self.log}


def retrieve_evidence(context, information_need: str, *, task: str = "BRTG",
                      max_tokens: int = 1000, budget: EvidenceBudget,
                      provider: str = "openai", model: str = "", host: str = "",
                      embed_provider: str = "ollama", embed_model: str = "",
                      chroma_dir=None) -> dict:
    need = (information_need or "").strip()
    if not need:
        return {"refused": "empty information_need", "answer": ""}

    cached = budget.seen(need)
    if cached:
        return {"answer": cached, "cached": True, "spent": budget.spent,
                "question": route(need)}
    if budget.exhausted():
        return {"refused": f"evidence budget exhausted ({budget.max_calls} "
                           f"retrieval(s) used); work from what the context holds",
                "answer": "", "spent": budget.spent}

    qid = route(need)
    question = next(q for q in nsm_q.QUESTIONS if q.id == qid)
    view = nsm_views.view_for(
        qid, nsm_views.VIEWS.get(context.view) or nsm_views.NO_DIFF)
    store = nsm_escalate._store(context, view, embed_provider=embed_provider,
                                embed_model=embed_model, host=host,
                                chroma_dir=chroma_dir)
    passages, tags = nsm_escalate._passages(store, question, view,
                                            category=context.category,
                                            extra_terms=need)
    data, usage, err = nsm_escalate._ask(
        question, ctx=context, passages=passages,
        instruction=(f"TARGETED EVIDENCE REQUEST ({task}): {need}\n"
                     f"Answer only this need. Keep it under roughly "
                     f"{max_tokens // 4} words."),
        provider=provider, model=model, host=host)

    budget.spent += 1
    budget.log.append({"need": need[:120], "question": qid,
                       "n_passages": len(passages),
                       "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                       "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                       "cost_usd": getattr(usage, "cost_usd", 0.0) or 0.0,
                       "error": err})
    if err or not data:
        return {"refused": err or "retrieval returned nothing", "answer": "",
                "spent": budget.spent, "question": qid}

    from NumericSpecificMiner.miner import _absorb, _usage_add
    _absorb(context, qid, data, tags)
    _usage_add(context.usage, usage)

    answer = _render(data, max_tokens)
    budget.answers[need] = answer
    context.expansions.append({"rung": "pevr_retrieve", "question": qid,
                               "need": need[:160],
                               "fields_added": sorted(data)})
    return {"answer": answer, "question": qid, "cached": False,
            "spent": budget.spent, "fields_added": sorted(data)}


def _render(data: dict, max_tokens: int) -> str:
    lines = []
    for key, value in data.items():
        if value in (None, "", [], {}) or key == "confidence":
            continue
        if isinstance(value, list):
            value = "; ".join(str(v)[:120] for v in value[:6])
        lines.append(f"{key}: {str(value)[:300]}")
    text = "\n".join(lines)
    return text[: max_tokens * 4]


_TOOL_SCHEMA = {
    "name": "retrieve_evidence",
    "description": ("Ask RepoMiner for ONE specific fact the reproduction context "
                    "does not contain. Use it only when the diagnosis names "
                    "something you cannot resolve from the context; the budget is "
                    "small and a repeated need returns the first answer."),
    "input_schema": {
        "type": "object",
        "properties": {
            "information_need": {"type": "string",
                                 "description": "the single fact needed, phrased "
                                                "as a question"},
            "task": {"type": "string", "enum": ["HBEC", "BRTG"]},
            "max_tokens": {"type": "integer", "default": 1000},
        },
        "required": ["information_need"],
    },
}


TOOL_SCHEMA = nsm_q.strict_objects(_TOOL_SCHEMA)

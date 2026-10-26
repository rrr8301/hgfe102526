"""LLM classification of a numerical bug into a 12-leaf taxonomy.

The keyword filter can only reach the special-value / convergence branches from
matched vocabulary; the fine leaves — and the entire *Correctness* branch (init/
update errors, wrong conditional, matrix-dtype, compiler semantics) — need
code-level reasoning. This module asks a provider (Anthropic/OpenAI/Ollama, via
:mod:`numbugminer.providers`) to read a bug's evidence and assign exactly one
leaf, with a confidence and rationale, or reject it as not-numerical.

    Numerical Bugs
    ├── Accuracy
    │   ├── Insufficient Precision Data Type
    │   ├── Inaccurate Arithmetic Expressions
    │   └── Ill-Conditioned Problem
    ├── Special-Value
    │   ├── Missing NaN Checks
    │   └── Overflow / Underflow
    ├── Convergence
    │   ├── Problematic Approximation Formula
    │   ├── Iterative Approximation Failure
    │   └── Divergence Caused by Special Values
    └── Correctness
        ├── Initialization / Update Errors in Iterative Solvers
        ├── Wrong Conditional Test
        ├── Vector / Matrix Data Type Errors
        └── Compiler Optimization Semantics Violations
"""

from __future__ import annotations

from dataclasses import dataclass

from . import providers

# category -> leaves, with a one-line definition per leaf for the prompt.
TAXONOMY: dict[str, dict[str, str]] = {
    "Accuracy": {
        "Insufficient Precision Data Type":
            "wrong/too-small float type (e.g. float32 where float64 is needed), precision loss",
        "Inaccurate Arithmetic Expressions":
            "catastrophic cancellation, bad rounding/truncation, numerically unstable formula rewrite",
        "Ill-Conditioned Problem":
            "the math problem itself is ill-conditioned; small input change -> large error",
    },
    "Special-Value": {
        "Missing NaN Checks":
            "NaN produced/propagated because a NaN or 0/0 case is not guarded",
        "Overflow / Underflow":
            "value exceeds the float range -> Inf, or underflows to 0, incl. premature overflow",
    },
    "Convergence": {
        "Problematic Approximation Formula":
            "the chosen series/approximation formula is wrong or inadequate for the regime",
        "Iterative Approximation Failure":
            "an iterative solver diverges, oscillates, or loops without converging",
        "Divergence Caused by Special Values":
            "iteration diverges specifically because a NaN/Inf entered the loop",
    },
    "Correctness": {
        "Initialization / Update Errors in Iterative Solvers":
            "wrong initial value or wrong update/step rule in an iterative method",
        "Wrong Conditional Test":
            "wrong branch/threshold selects the incorrect approximation or code path",
        "Vector / Matrix Data Type Errors":
            "shape/dtype/layout mistakes in vector or matrix operations",
        "Compiler Optimization Semantics Violations":
            "fast-math / reordering / flags change floating-point semantics",
    },
}
CATEGORIES = list(TAXONOMY)
LEAVES = [leaf for leaves in TAXONOMY.values() for leaf in leaves]

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "is_numerical_bug": {"type": "boolean"},
        "category": {"type": "string", "enum": CATEGORIES + ["Not Numerical"]},
        "leaf": {"type": "string", "enum": LEAVES + ["None"]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
    },
    "required": ["is_numerical_bug", "category", "leaf", "confidence", "rationale"],
}


def _system() -> str:
    lines = ["You classify a software bug into a numerical-bug taxonomy. Read the "
             "EVIDENCE (issue text, failing-test trace, modified files) and assign the "
             "SINGLE best-fitting leaf. If the evidence shows it is not actually a "
             "floating-point numerical bug, set is_numerical_bug=false, "
             "category='Not Numerical', leaf='None'. Judge from the evidence, not the "
             "keyword that flagged it. Taxonomy (category -> leaf: definition):"]
    for cat, leaves in TAXONOMY.items():
        for leaf, desc in leaves.items():
            lines.append(f"- [{cat}] {leaf}: {desc}")
    lines.append("Return a single JSON object matching the schema; leaf must belong to category.")
    return "\n".join(lines)


@dataclass
class TaxonomyResult:
    category: str
    leaf: str
    is_numerical_bug: bool
    confidence: float
    rationale: str
    usage: object | None = None

    def to_dict(self) -> dict:
        d = {"category": self.category, "leaf": self.leaf,
             "is_numerical_bug": self.is_numerical_bug,
             "confidence": self.confidence, "rationale": self.rationale}
        if self.usage is not None:
            d["cost_usd"] = self.usage.cost_usd
        return d


def classify(evidence: str, *, provider: str = "anthropic", model: str | None = None,
             host: str = providers.OLLAMA_HOST, max_tokens: int = 700) -> TaxonomyResult:
    """Classify one bug's `evidence` into a taxonomy leaf."""
    model = model or providers.DEFAULT_CHAT_MODEL[provider]
    user = f"EVIDENCE:\n{evidence[:8000]}\n\nClassify this bug per the schema."
    res = providers.chat(provider, model, _system(), user, schema=_SCHEMA, host=host,
                         max_tokens=max_tokens)
    try:
        d = providers.extract_json(res.text)
    except ValueError:
        return TaxonomyResult("Not Numerical", "None", False, 0.0,
                              "unparseable model response", res.usage)
    return TaxonomyResult(
        category=d.get("category", "Not Numerical"),
        leaf=d.get("leaf", "None"),
        is_numerical_bug=bool(d.get("is_numerical_bug", False)),
        confidence=float(d.get("confidence", 0.0) or 0.0),
        rationale=d.get("rationale", ""),
        usage=res.usage,
    )

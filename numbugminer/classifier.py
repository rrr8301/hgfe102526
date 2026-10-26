from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from .costs import Usage
from .retriever import Retrieved

# One of these buckets; keep in sync with the seed corpus bug_type values.
BUG_TYPES = [
    "nan",
    "inf",
    "overflow",
    "underflow",
    "divide_by_zero",
    "not_finite",
    "convergence",
    "linalg",
    "other",
    "none",
]

_SYSTEM = (
    "You are a numerical-bug triage assistant for CI failure logs and pull "
    "requests. A numerical bug is a defect where floating-point computation goes "
    "wrong: NaN, Inf, overflow, underflow, divide-by-zero, loss of a finiteness "
    "invariant, or solver non-convergence/ill-conditioning caused by the "
    "arithmetic. Environment, dependency, network, OOM, timeout, syntax, and "
    "ordinary logic/assertion failures are NOT numerical bugs, even if they "
    "mention numbers. Judge only from the evidence in the excerpt. If it lacks a "
    "clear numerical signal, prefer is_numerical_bug=false with low confidence "
    "rather than guessing.\n\n"
    "When the excerpt is a pull request, ALSO judge its commits: set "
    "writes_numerical_test=true only if a commit adds or extends a test that "
    "exercises THIS numerical bug (e.g. a new test asserting a result is finite, "
    "not-NaN, within tolerance, or that a formerly-crashing input now works). A "
    "commit that changes only production code, or adds a test unrelated to the "
    "numerical behaviour, is writes_numerical_test=false. If it is not a "
    "numerical bug, writes_numerical_test must be false."
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_numerical_bug": {"type": "boolean"},
        "bug_type": {"type": "string", "enum": BUG_TYPES},
        "confidence": {"type": "number"},
        "writes_numerical_test": {"type": "boolean"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["line", "reason"],
                "additionalProperties": False,
            },
        },
        "explanation": {"type": "string"},
    },
    "required": ["is_numerical_bug", "bug_type", "confidence",
                 "writes_numerical_test", "evidence", "explanation"],
    "additionalProperties": False,
}


class Evidence(BaseModel):
    line: str
    reason: str


class Verdict(BaseModel):
    is_numerical_bug: bool
    bug_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence]
    explanation: str
    # Whether the PR's commits add/extend a test exercising the numerical bug.
    # Defaulted so the RAG-free keyword path (which has no commit view) is valid.
    writes_numerical_test: bool = False


def _format_examples(retrieved: list[Retrieved]) -> str:
    blocks = []
    for r in retrieved:
        verdict = "NUMERICAL BUG" if r.example.is_numerical else "NOT a numerical bug"
        blocks.append(
            f"[example {r.example.id} | {verdict} | type={r.example.bug_type} "
            f"| similarity={r.score:.2f}]\n{r.example.snippet}\n({r.example.description})"
        )
    return "\n\n".join(blocks)


def build_prompt(excerpt: str, retrieved: list[Retrieved], signals: set[str]) -> str:
    signal_note = (
        f"Regex prescreen flagged these numerical signals: {', '.join(sorted(signals))}."
        if signals
        else "Regex prescreen found no obvious numerical signal."
    )
    return (
        "Retrieved labeled examples (nearest neighbours in the knowledge base):\n\n"
        f"{_format_examples(retrieved)}\n\n"
        "=======================================================\n"
        f"{signal_note}\n\n"
        "Now classify THIS excerpt. Decide is_numerical_bug/bug_type/confidence, "
        "and — using the 'TEST-WRITING COMMITS' section and diff hunks below — "
        "also decide writes_numerical_test (does a commit add/extend a test for "
        "this numerical bug?). Cite the deciding lines in evidence.\n\n"
        f"{excerpt}"
    )


# Adaptive thinking is only accepted on Opus 4.6+/Sonnet 5/4.6/Fable 5; Haiku 4.5
# and older reject it with a 400, so we simply omit thinking there.
_ADAPTIVE_THINKING_MODELS = (
    "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
    "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5", "claude-mythos-5",
)


def _supports_adaptive_thinking(model: str) -> bool:
    return any(model.startswith(m) for m in _ADAPTIVE_THINKING_MODELS)


def _extract_text(content: list) -> str:
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("No text block in Claude response.")


def classify(
    client,
    model: str,
    excerpt: str,
    retrieved: list[Retrieved],
    signals: set[str],
    max_tokens: int = 2000,
) -> tuple[Verdict, "Usage"]:
    """Return the verdict and the token :class:`~numbugminer.costs.Usage` it cost."""
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": build_prompt(excerpt, retrieved, signals)}],
    )
    if _supports_adaptive_thinking(model):
        kwargs["thinking"] = {"type": "adaptive"}
    resp = client.messages.create(**kwargs)
    usage = Usage.from_response(getattr(resp, "model", model) or model, resp.usage)
    text = _extract_text(resp.content)
    try:
        return Verdict.model_validate(json.loads(text)), usage
    except (json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"Could not parse Claude verdict: {e}\nRaw: {text[:500]}") from e

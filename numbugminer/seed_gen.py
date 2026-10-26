"""Meta-prompted construction of the seed corpus.

Instead of hand-typing every labeled example, a *meta-prompt* asks an LLM to
synthesize diverse, realistic examples for each slot in the numerical-bug
taxonomy. The labels stay trustworthy because the code — not the model — fixes
`label` and `bug_type` per slot; the model only invents the `snippet` and
`description`. Output is schema-validated, deduped, and merged on top of a
neutral hand-anchor base so curated examples are never lost.

Generation is provider-agnostic. Each provider writes to its own corpus file so
the Claude-generated and OpenAI-generated seed sets stay separate and
comparable:

    numbugminer seed-gen --provider anthropic          # -> data/seed_corpus.claude.jsonl
    numbugminer seed-gen --provider openai             # -> data/seed_corpus.openai.jsonl
    numbugminer seed-gen --provider openai --replace   # rebuild from hand anchors

This is the "seed set construction" half of the benchmark: grow or regenerate
the knowledge base that backs RAG retrieval.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .knowledge_base import Example, load_corpus

# Numerical-bug slots: (bug_type, what it is / how it typically surfaces).
NUMERICAL_SLOTS: list[tuple[str, str]] = [
    ("nan", "a computation yields NaN (0/0, inf-inf, log of non-positive, sqrt of negative)"),
    ("inf", "a finite value becomes +/-inf (exp/overflow of a float, 1/0 in float math)"),
    ("overflow", "a value exceeds the representable range (float exp overflow, int accumulator wrap)"),
    ("underflow", "a small value collapses to 0, losing information (product of many probabilities)"),
    ("divide_by_zero", "division by a zero denominator (ZeroDivisionError, RuntimeWarning divide by zero)"),
    ("not_finite", "a finiteness invariant is violated (isfinite/isnan assertion, 'contains NaN or infinity')"),
    ("convergence", "an iterative solver fails to converge or diverges (max iterations, residual too high)"),
    ("linalg", "a linear-algebra routine fails numerically (singular matrix, not positive definite, SVD did not converge)"),
    ("other", "another genuine floating-point defect: catastrophic cancellation, loss of precision, unsafe float equality, missing tolerance"),
]

# Non-bug slots (all label=not_numerical_bug, bug_type=none): things that fail
# in CI but are NOT floating-point defects. The last few are deliberately
# tricky (they mention numbers/NaN) to teach the classifier discrimination.
NONBUG_SLOTS: list[tuple[str, str]] = [
    ("dependency", "an import / missing-package / dependency-resolution failure"),
    ("environment", "an environment or CI-infrastructure failure (network, download, runner shutdown)"),
    ("resource", "out-of-memory / resource exhaustion (OOM kill, disk full), even on large numeric arrays"),
    ("syntax", "a syntax or compile error"),
    ("logic", "an ordinary logic/indexing bug (off-by-one, wrong branch) that involves integers but not float behavior"),
    ("timeout", "a flaky test / timeout / performance regression"),
    ("io", "a file-not-found / IO / serialization error"),
    ("incidental_numbers", "a change that MENTIONS nan/inf/overflow/precision incidentally (docs, log-level names, a variable literally called 'nan') but changes no numerical behavior"),
]

_SYSTEM = (
    "You construct a high-quality labeled SEED CORPUS for a retrieval-augmented "
    "classifier that decides whether a GitHub pull request is a FLOATING-POINT "
    "NUMERICAL BUG. Each example must read like real evidence pulled from a PR: "
    "a PR title plus a short unified-diff hunk, or a linked-issue description, or "
    "a CI log fragment (RuntimeWarning / Traceback / exception line). Draw on the "
    "flavor of real scientific-Python libraries (numpy, scipy, scikit-learn, "
    "pytorch, jax, statsmodels). Keep each snippet concise (a few lines, under "
    "~600 characters) and self-contained. Vary the surface form across examples. "
    "The 'description' is one sentence stating WHY the example has its label — the "
    "reasoning a triager would give. Never wrap output in markdown fences."
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "snippet": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["id", "snippet", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["examples"],
    "additionalProperties": False,
}


class _GenExample(BaseModel):
    id: str
    snippet: str
    description: str


class _GenBatch(BaseModel):
    examples: list[_GenExample]


@dataclass
class GenStats:
    generated: int
    kept: int
    duplicates: int
    existing: int
    total: int
    out_path: Path


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:40] or "example"


def _norm(snippet: str) -> str:
    return re.sub(r"\s+", " ", snippet.strip().lower())


def _extract_text(content: list) -> str:
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise RuntimeError("No text block in Claude response.")


def _structured_generate(
    provider: str, client, model: str, system: str, prompt: str, max_tokens: int
) -> str:
    """Provider-agnostic schema-constrained generation; returns raw JSON text."""
    if provider == "anthropic":
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        return _extract_text(resp.content)

    if provider == "openai":
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "seed_examples", "schema": _SCHEMA, "strict": True},
            },
        )
        return resp.choices[0].message.content or ""

    raise ValueError(f"Unknown provider: {provider!r} (use 'anthropic' or 'openai').")


def _build_prompt(label: str, name: str, kind: str, n: int) -> str:
    if label == "numerical_bug":
        header = (
            f"Generate {n} DISTINCT examples that ARE floating-point numerical bugs "
            f"of type '{name}': {kind}."
        )
    else:
        header = (
            f"Generate {n} DISTINCT examples that are NOT floating-point numerical bugs. "
            f"Category '{name}': {kind}."
        )
    return (
        f"{header}\n\n"
        "Requirements:\n"
        "- Each example: a short realistic evidence snippet + a one-sentence "
        "'description' explaining the label.\n"
        "- Make the examples diverse (different libraries, different surface forms: "
        "PR title+diff, issue text, or CI log line).\n"
        "- 'id' should be a short lowercase_snake_case slug unique within this batch.\n"
        "- Do NOT restate the label verbatim; describe the underlying reason."
    )


def _generate_slot(
    provider: str, client, model: str, label: str, bug_type: str, slot_key: str,
    name: str, kind: str, n: int, max_tokens: int, source: str
) -> list[Example]:
    text = _structured_generate(
        provider, client, model, _SYSTEM, _build_prompt(label, name, kind, n), max_tokens
    )
    try:
        batch = _GenBatch.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise RuntimeError(f"Bad generation for {label}/{slot_key}: {e}\nRaw: {text[:300]}") from e

    out: list[Example] = []
    for g in batch.examples:
        out.append(Example(
            id=f"{slot_key}__{_slug(g.id)}",
            label=label,
            bug_type=bug_type,
            snippet=g.snippet.strip(),
            description=g.description.strip(),
            source=source,
        ))
    return out


def _dedup_merge(
    existing: list[Example], generated: list[Example]
) -> tuple[list[Example], int]:
    """Merge, dropping generated items whose id or snippet already appears."""
    seen_ids = {e.id for e in existing}
    seen_snips = {_norm(e.snippet) for e in existing}
    merged = list(existing)
    dups = 0
    for g in generated:
        gid, gsnip = g.id, _norm(g.snippet)
        if gid in seen_ids or gsnip in seen_snips:
            dups += 1
            continue
        seen_ids.add(gid)
        seen_snips.add(gsnip)
        merged.append(g)
    return merged, dups


def _write_corpus(path: Path, examples: list[Example]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in examples:
            f.write(json.dumps({
                "id": e.id, "label": e.label, "bug_type": e.bug_type,
                "snippet": e.snippet, "description": e.description, "source": e.source,
            }, ensure_ascii=False) + "\n")


def generate_seed_corpus(
    client,
    model: str,
    out_path: Path,
    *,
    provider: str = "anthropic",
    per_type: int = 3,
    merge: bool = True,
    base_path: Path | None = None,
    max_tokens: int = 4000,
    progress=lambda msg: None,
) -> GenStats:
    """Meta-prompt a provider to build the seed corpus, validate, dedup, write.

    The corpus starts from `existing` = the current provider file (when merging)
    or, if that's absent, the neutral hand-anchor `base_path`, so every provider
    corpus shares the same curated anchors. Generated examples are tagged with a
    provider-specific `source` ("meta:anthropic" / "meta:openai").
    """
    source = f"meta:{provider}"
    existing: list[Example] = []
    if merge and out_path.exists():
        existing = load_corpus(out_path)
    elif base_path and base_path.exists():
        existing = load_corpus(base_path)
    n_existing = len(existing)

    generated: list[Example] = []
    for bug_type, kind in NUMERICAL_SLOTS:
        progress(f"[{provider}] numerical/{bug_type} …")
        generated += _generate_slot(provider, client, model, "numerical_bug", bug_type,
                                     slot_key=bug_type, name=bug_type, kind=kind,
                                     n=per_type, max_tokens=max_tokens, source=source)
    for category, kind in NONBUG_SLOTS:
        progress(f"[{provider}] non-bug/{category} …")
        generated += _generate_slot(provider, client, model, "not_numerical_bug", "none",
                                     slot_key=f"not_{category}", name=category, kind=kind,
                                     n=per_type, max_tokens=max_tokens, source=source)

    # Dedup generated against itself first, then merge with existing.
    self_merged, self_dups = _dedup_merge([], generated)
    merged, ext_dups = _dedup_merge(existing, self_merged)
    _write_corpus(out_path, merged)

    return GenStats(
        generated=len(generated),
        kept=len(merged) - n_existing,
        duplicates=self_dups + ext_dups,
        existing=n_existing,
        total=len(merged),
        out_path=out_path,
    )

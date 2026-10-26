"""Provenance isolation, enforced and checked rather than asserted (DP5).

Three layers, weakest to strongest:

1. **Structural** — a withheld artifact is never indexed in that view's
   collection, so it cannot be retrieved (`views.filter_chunks`).
2. **Citation** — every fact the model writes must cite a passage that was
   actually supplied; a fact citing anything else is dropped before it reaches
   the context (`attribute`).
3. **Fingerprint** — the finished context is checked for distinctive n-grams
   from the withheld text. If a value echoes the developer's test, it is caught
   here even if layers 1 and 2 were bypassed (`leak_report`).

Layer 3 exists because the first two trust the pipeline; a benchmark that claims
information isolation should be able to demonstrate it on the artifact itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .context import NumericalContext
from .views import VIEWS, View

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z_0-9.]{3,}|[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?")
#: words common to any Python project — matching on them proves nothing
_NOISE = {
    "import", "return", "assert", "self", "none", "true", "false", "test", "tests",
    "value", "values", "array", "numpy", "np.array", "result", "expected", "def",
    "class", "with", "from", "this", "that", "should", "would", "when", "which",
    "data", "dtype", "float", "int", "bool", "print", "python", "the",
}


def _tokens(text: str) -> set:
    """The distinctive tokens a text contains, lowercased."""
    return {t.lower() for t in _TOKEN.findall(text or "")} - _NOISE


def _fingerprints(text: str, n: int = 3) -> set:
    """Distinctive n-grams of code-ish tokens, lowercased."""
    toks = [t.lower() for t in _TOKEN.findall(text or "")]
    toks = [t for t in toks if t not in _NOISE]
    return {" ".join(toks[i:i + n]) for i in range(max(0, len(toks) - n + 1))}


def _context_text(ctx: NumericalContext) -> str:
    """Only the extracted VALUES — retrieval refs legitimately name files."""
    parts = []
    for section in (ctx.environment, ctx.numerical_failure,
                    ctx.numerical_characteristics):
        for v in section.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts.extend(str(x) for x in v)
    return "\n".join(parts)


@dataclass
class AuditResult:
    view: str
    withheld: list
    citation_violations: list
    leaked_ngrams: list
    ok: bool

    def as_dict(self) -> dict:
        return {"view": self.view, "withheld": self.withheld,
                "citation_violations": self.citation_violations,
                "leaked_ngrams": self.leaked_ngrams[:10], "ok": self.ok}


def attribute(data: dict, supplied_tags: list, view: View) -> tuple:
    """Keep only what cites a supplied passage from an allowed source.

    Returns `(kept, dropped)`. A model that cites nothing keeps its answer — the
    citation layer is about forbidden sources, not about forcing a citation
    format the schema does not demand — but a model that cites a withheld source
    loses that field, because the only way it could cite it is to have seen it.
    """
    allowed_sources = {t.split(":", 1)[0] for t in supplied_tags}
    dropped = []
    kept = {}
    for key, value in (data or {}).items():
        cited = ""
        if isinstance(value, dict):
            cited = str(value.get("source") or "")
        if cited and (cited in view.withheld or
                      (allowed_sources and cited not in allowed_sources)):
            dropped.append({"field": key, "cited": cited})
            continue
        kept[key] = value
    return kept, dropped


def leak_report(ctx: NumericalContext, withheld_text: str,
                allowed_text: str = "", min_hits: int = 2) -> list:
    """N-grams the context shares with text it was not allowed to see, and that
    it could NOT have got from text it was allowed to see.

    The second clause is what makes this a leak test rather than a similarity
    test. An issue that pastes the same reproduction snippet the developer's
    test uses — `test_gcd_overflow`, `assert_equal(np.gcd(...), q)` — puts that
    content in an ALLOWED source, and a context quoting it has leaked nothing.
    Subtracting the allowed fingerprints leaves only content whose sole possible
    origin is withheld.

    `min_hits` then guards against the residue of coincidence.
    """
    if not withheld_text.strip():
        return []
    shared = _fingerprints(_context_text(ctx)) & _fingerprints(withheld_text)
    shared -= _fingerprints(allowed_text)
    # Subtracting the allowed n-grams is not enough on its own, because the
    # context is PARAPHRASE, not copied text. A model writing about `cov_nearest`
    # and `min_diag` in its own words produces trigrams like "min_diag raised
    # min_diag" that also occur in the diff, although every token came from the
    # PR title. Same subject, same identifiers, different order.
    #
    # So an n-gram only counts as a leak when it carries at least one TOKEN that
    # appears NOWHERE in the allowed text: vocabulary the context could not have
    # acquired except from the withheld source. Word order is coincidence;
    # unknown vocabulary is evidence.
    allowed_tokens = _tokens(allowed_text)
    shared = {g for g in shared
              if any(t not in allowed_tokens for t in g.split())}
    return sorted(shared) if len(shared) >= min_hits else []


def audit(ctx: NumericalContext, *, withheld_text: str = "",
          allowed_text: str = "", citation_violations: list | None = None) -> AuditResult:
    """The isolation record that travels with a mined context."""
    view = VIEWS.get(ctx.view) or VIEWS["no_diff"]
    leaks = leak_report(ctx, withheld_text, allowed_text)
    violations = list(citation_violations or [])
    return AuditResult(view=view.name, withheld=list(view.withheld),
                       citation_violations=violations, leaked_ngrams=leaks,
                       ok=not leaks and not violations)

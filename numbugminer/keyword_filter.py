from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .classifier import Evidence, Verdict

# --- The keyword vocabulary --------------------------------------------------
# (keyword, weight, bug_type, regex). Word boundaries keep "nan" out of
# "nanometer" and "inf" concepts out of "information"; the inflection groups
# absorb plurals / verb forms without letting a stem swallow a longer sibling
# ("cancel" must NOT eat "cancellation" — they mean different things here).
#
# STRONG (weight 1.0): a hit is, on its own, strong evidence of an FP defect.
# WEAK   (weight 0.4): appears in numerical contexts but also plenty elsewhere,
#                      so it only tips the verdict when it corroborates others.
_STRONG = 1.0
_WEAK = 0.4


@dataclass(frozen=True)
class _Term:
    keyword: str
    weight: float
    bug_type: str
    pattern: re.Pattern[str]


def _t(keyword: str, weight: float, bug_type: str, regex: str) -> _Term:
    return _Term(keyword, weight, bug_type, re.compile(regex, re.I))


# Ordered by descending specificity; ties broken by this order when choosing the
# reported bug_type. bug_type values are kept in classifier.BUG_TYPES.
VOCABULARY: list[_Term] = [
    # --- strong, and each maps to a concrete bug_type bucket ------------------
    _t("nan",          _STRONG, "nan",         r"\bnan\b"),
    _t("infinity",     _STRONG, "inf",         r"\binfinity\b"),
    _t("infinite",     _STRONG, "inf",         r"\binfinite\b"),
    _t("overflow",     _STRONG, "overflow",    r"\boverflow(?:s|ed|ing)?\b"),
    _t("underflow",    _STRONG, "underflow",   r"\bunderflow(?:s|ed|ing)?\b"),
    _t("diverge",      _STRONG, "convergence", r"\bdiverg(?:e|es|ed|ing|ence|ent)\b"),
    _t("unstable",     _STRONG, "convergence", r"\bunstable\b"),
    _t("instability",  _STRONG, "convergence", r"\binstabilit(?:y|ies)\b"),
    # A convergence defect is usually reported as a FAILURE TO converge, and the
    # vocabulary carried only the opposites — so "does not converge", the most
    # natural wording of the category, scored zero and never reached the RAG
    # stage. These phrases are strong: no benign PR says its solver stopped
    # converging. The bare word is weak, because "the branches converged" and
    # "converged on a design" are ordinary English.
    _t("not_converging", _STRONG, "convergence",
       r"\b(?:not|n't|never|fail(?:s|ed|ing)?\s+to|unable\s+to|does\s+not|doesn't|"
       r"stops?|stopped)\s+converg\w*\b|\bnon[-\s]?converg\w*\b|"
       r"\bconvergence\s+(?:failure|issues?|problems?|bug|error)\b"),
    # Strong only WITH the failure context: a bare `maxiter` is an ordinary
    # parameter name that appears in any diff snippet, and treating it as strong
    # evidence let a GCC-version bump into the candidate set.
    _t("max_iterations", _STRONG, "convergence",
       r"\b(?:max(?:imum)?[\s_-]?iter(?:ation)?s?|iteration\s+limit)\b"
       r"[^.\n]{0,40}\b(?:exceed\w*|reach\w*|hit|without\s+converg\w*|"
       r"before\s+converg\w*|still\s+not)\b"
       r"|\b(?:exceed\w*|reach\w*|hit)\b[^.\n]{0,20}"
       r"\b(?:max(?:imum)?[\s_-]?iter(?:ation)?s?|iteration\s+limit)\b"),
    _t("maxiter",      _WEAK,   "convergence",
       r"\bmax(?:imum)?[\s_-]?iter(?:ation)?s?\b"),
    _t("stagnate",     _STRONG, "convergence", r"\bstagnat(?:e|es|ed|ing|ion)\b"),
    # Di Franco et al. count converging TOO SLOWLY as the same defect as not
    # converging at all — the iteration still terminates, just uselessly late or
    # on a budget it should not have needed.
    _t("slow_convergence", _STRONG, "convergence",
       r"\bslow(?:ly)?\s+converg\w*\b|\bconverg\w*\s+(?:too\s+)?slow(?:ly)?\b"
       r"|\bslow\s+convergence\b|\btakes?\s+too\s+many\s+iterations?\b"),
    # instability in their sense: the output is sensitive to small variations in
    # inputs or intermediate quantities, so rounding drives it far from correct
    _t("ill_conditioned", _STRONG, "convergence",
       r"\bill[-\s]?conditioned?\b|\bill[-\s]?conditioning\b"),
    _t("oscillate",    _WEAK,   "convergence", r"\boscillat(?:e|es|ed|ing|ion|ory)\b"),
    _t("sensitivity",  _WEAK,   "convergence",
       r"\bsensitiv(?:e|ity)\s+to\b|\bcondition\s+number\b"),
    _t("error_estimate", _WEAK, "convergence",
       r"\berror\s+estimat\w*\b|\bstopping\s+criteri\w*\b|\btermination\s+criteri\w*\b"),
    # The definition's own example: a loop meant to iterate until an error
    # estimate falls below a tolerance, terminating on the wrong side of that
    # test. Instability around the conditional IS the defect, so the phrase is
    # strong even though its parts ("exits", "tolerance") are ordinary.
    _t("premature_termination", _STRONG, "convergence",
       # "exit early" is ordinary control flow; it is only this defect when the
       # thing exiting early is an ITERATION. The context word is therefore part
       # of the pattern, on either side of the phrase, within one sentence.
       r"\b(?:iterat\w*|converg\w*|solver|toleran\w*|residual|refinement|newton"
       r"|approximation|series)\b[^.\n]{0,60}"
       r"\b(?:too\s+early|prematurely|premature|early)\s*"
       r"(?:termination|exit|stop|return|exits?|terminat\w*|stops?)?\b"
       r"|\b(?:too\s+early|prematurely|premature|early)\b[^.\n]{0,60}"
       r"\b(?:iterat\w*|converg\w*|solver|toleran\w*|residual|refinement|newton"
       r"|approximation|series)\b"
       # the definition's archetype: the loop leaves before its error test passes
       r"|\b(?:exits?|terminat\w*|stops?|breaks?)\b[^.\n]{0,40}\bbefore\b[^.\n]{0,40}"
       r"\b(?:converg\w*|toleran\w*|error\s+estimat\w*|below)\b"
       r"|\b(?:stopping|termination)\s+criteri\w*[^.\n]{0,40}"
       r"\b(?:wrong|incorrect|never|too\s+\w+|before)\b"),
    _t("converge",     _WEAK,   "convergence", r"\bconverg(?:e|es|ed|ing|ence|ent)\b"),
    _t("residual",     _WEAK,   "convergence", r"\bresiduals?\b"),
    _t("tolerance",    _WEAK,   "convergence", r"\btolerances?\b|\br?tol\b|\batol\b"),
    # --- strong, but no dedicated bucket -> "other" ---------------------------
    _t("cancellation", _STRONG, "other",       r"\bcancellations?\b"),
    _t("roundoff",     _STRONG, "other",       r"\bround[\s-]?off\b"),
    _t("truncation",   _STRONG, "other",       r"\btruncation\b"),
    _t("unbounded",    _STRONG, "other",       r"\bunbounded\b"),
    _t("ringing",      _STRONG, "other",       r"\bringing\b"),
    # --- weak / ambiguous: need corroboration to tip the verdict --------------
    _t("precision",    _WEAK,   "other",       r"\bprecision\b"),
    _t("rounding",     _WEAK,   "other",       r"\brounding\b|\brounded\b"),
    _t("accuracy",     _WEAK,   "other",       r"\baccuracy\b"),
    _t("accurate",     _WEAK,   "other",       r"\baccurate\b|\binaccurate\b"),
    _t("exception",    _WEAK,   "other",       r"\bexceptions?\b"),
    # "cancel" (killed CI job) is deliberately separated from "cancellation"
    # (catastrophic cancellation) — different meanings, so different weights.
    # The \b after the optional suffix can't fall inside "cancellation", so this
    # never double-counts with the "cancellation" term above.
    _t("cancel",       _WEAK,   "other",       r"\bcancel(?:s|led|ling|ed)?\b"),
]

# Default: fire when the distinct matched keywords sum to at least one strong
# hit (or ~2.5 weak ones). One "exception" or one "precision" alone won't fire.
DEFAULT_THRESHOLD = 1.0


@dataclass(frozen=True)
class KeywordHit:
    keyword: str
    weight: float
    bug_type: str
    count: int          # total occurrences in the text
    first_line: str     # the first line the keyword appeared on (evidence)


def scan(text: str) -> list[KeywordHit]:
    """All distinct vocabulary keywords present in `text`, with counts + a line."""
    lines = (text or "").splitlines()
    hits: list[KeywordHit] = []
    for term in VOCABULARY:
        count = 0
        first_line = ""
        for line in lines:
            n = len(term.pattern.findall(line))
            if n:
                count += n
                if not first_line:
                    first_line = line.strip()[:200]
        if count:
            hits.append(KeywordHit(term.keyword, term.weight, term.bug_type,
                                   count, first_line))
    return hits


def _confidence(score: float, threshold: float) -> float:
    """Map a keyword score onto [0,1), centred at 0.5 on the threshold."""
    if score <= 0:
        return 0.0
    if score < threshold:
        return round(0.5 * (score / threshold), 3)
    # Above threshold: approach 1.0 as more (or stronger) keywords pile up.
    return round(min(0.98, 0.5 + 0.5 * (1.0 - math.exp(-(score - threshold)))), 3)


def _pick_bug_type(hits: list[KeywordHit]) -> str:
    """The bucket of the strongest matched keyword (VOCABULARY order breaks ties)."""
    order = {t.keyword: i for i, t in enumerate(VOCABULARY)}
    best = min(hits, key=lambda h: (-h.weight, order[h.keyword]))
    return best.bug_type


def classify(text: str, threshold: float = DEFAULT_THRESHOLD) -> Verdict:
    """Decide whether `text` describes a numerical bug from keywords alone."""
    hits = scan(text)
    score = sum(h.weight for h in hits)  # distinct keywords, weighted
    is_bug = score >= threshold

    if not hits:
        return Verdict(
            is_numerical_bug=False,
            bug_type="none",
            confidence=0.0,
            evidence=[],
            explanation="No numerical-bug keyword matched the evidence.",
        )

    matched = ", ".join(f"{h.keyword}(x{h.count})" for h in hits)
    evidence = [
        Evidence(line=h.first_line or f"<keyword: {h.keyword}>",
                 reason=f"matched keyword '{h.keyword}' "
                        f"(weight {h.weight:g}, {h.count} occurrence(s))")
        for h in hits
    ]
    verb = "meets" if is_bug else "below"
    explanation = (
        f"Keyword filter matched {len(hits)} distinct term(s) [{matched}] "
        f"for a weighted score of {score:.2f}, which {verb} the "
        f"threshold {threshold:g}. No RAG / model call was used."
    )
    return Verdict(
        is_numerical_bug=is_bug,
        bug_type=_pick_bug_type(hits) if is_bug else "none",
        confidence=_confidence(score, threshold),
        evidence=evidence,
        explanation=explanation,
    )


# --------------------------------------------------------------------------- #
# bug-fixing intent — the second half of the prescreen
# --------------------------------------------------------------------------- #
# Numerical relevance alone is not a candidate: a PR that ADDS a numerical
# feature, or a release note listing "precision improvements", trips the
# vocabulary above without fixing any defect. Retention therefore requires
# numerical relevance AND evidence that the PR intends to fix a bug.

#: Repository labels that state the intent outright. Compared case-folded, and
#: by substring, so "type: bug", "kind/bug" and "Bug Report" all count.
BUG_LABEL_TERMS = ("bug", "defect", "regression", "fix", "error", "incorrect")

#: Defect-oriented terms in the PR title. Deliberately narrow: "fix"/"fixes"
#: and "bug" carry the intent, while "improve" or "update" do not.
_BUG_TITLE = (
    ("fix", re.compile(r"\bfix(?:e[sd]|ing)?\b", re.I)),
    ("bug", re.compile(r"\bbugs?\b", re.I)),
    ("regression", re.compile(r"\bregressions?\b", re.I)),
    ("incorrect", re.compile(r"\bincorrect(?:ly)?\b|\bwrong\b", re.I)),
    ("broken", re.compile(r"\bbroken\b", re.I)),
)


def bug_indicators(title: str, labels=()) -> list[str]:
    """Evidence that this PR fixes a defect: matched title terms and bug labels.

    Returns the indicators found (empty when there are none), so the crawler can
    record WHY a candidate was kept rather than only that it was.
    """
    found = []
    for name, pattern in _BUG_TITLE:
        if pattern.search(title or ""):
            found.append(name)
    for label in labels or ():
        low = str(label).lower()
        if any(term in low for term in BUG_LABEL_TERMS):
            found.append(f"label:{label}")
    return found

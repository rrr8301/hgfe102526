"""Task-specific evidence views, applied BEFORE retrieval (DP5).

The two reproduction tasks may see different artifacts, and the filtering has to
happen before the index is built rather than after a profile is written: a
summary produced from the developer's regression test leaks that test's content
however carefully the profile is worded afterwards.

Each view is therefore a different Chroma collection, built from a different
subset of the chunks, and answered in a fresh generation context.

    NO_DIFF          the report and the repository, for EVERY question — the
                     default: no diff of any kind is indexed, so no question,
                     repair or escalation can read one
    HBEC             everything, including the fix and the developer's test
    BRTG_NO_DIFF     the report and the repository for the BRTG questions, while
                     the environment question still reads the fix in HBEC
    BRTG_WITH_DIFF   the report, the repository and the SOURCE fix, still no test

The access matrix below is the one in the design, encoded so a reviewer can read
it off the code rather than trusting prose.
"""
from __future__ import annotations

from dataclasses import dataclass

#: chunk `source_type` values produced by the document builder
PR_INFO = ("pr_body", "pr_meta", "issue", "discussion", "commit", "web")
REPO = ("readme", "config", "source")
SOURCE_DIFF = ("diff",)
TEST_DIFF = ("test_diff",)


@dataclass(frozen=True)
class View:
    name: str
    allowed: tuple
    #: what the view deliberately withholds, kept for the record that travels
    #: with every context this view produced
    withheld: tuple
    note: str

    def keeps(self, source_type: str) -> bool:
        return source_type in self.allowed


HBEC = View(
    name="hbec",
    allowed=PR_INFO + REPO + SOURCE_DIFF + TEST_DIFF,
    withheld=(),
    note="environment construction may see everything, including the fix and the "
         "developer's regression test: the environment is not the thing being "
         "predicted",
)

BRTG_NO_DIFF = View(
    name="brtg_no_diff",
    allowed=PR_INFO + REPO,
    withheld=SOURCE_DIFF + TEST_DIFF,
    note="test generation from the report alone: neither the source fix nor the "
         "developer's test may reach the index, because both encode the answer",
)

BRTG_WITH_DIFF = View(
    name="brtg_with_diff",
    allowed=PR_INFO + REPO + SOURCE_DIFF,
    withheld=TEST_DIFF,
    note="test generation that may read the SOURCE fix — the setting where the "
         "patch is given but the developer's test is still withheld",
)

NO_DIFF = View(
    name="no_diff",
    allowed=PR_INFO + REPO,
    withheld=SOURCE_DIFF + TEST_DIFF,
    note="no diff of any kind is indexed, for any question. BRTG_NO_DIFF still "
         "lets the environment question read the fix in the HBEC collection, "
         "which is defensible — the environment is not what is predicted — but it "
         "means the run as a whole did consume the diff. This view removes that "
         "asymmetry: everything downstream, including the repair re-asks and the "
         "escalation ladder, is answered from the report and the repository alone",
)

VIEWS = {v.name: v for v in (NO_DIFF, HBEC, BRTG_NO_DIFF, BRTG_WITH_DIFF)}

#: views in which no question may read a diff, so the per-question override below
#: does not apply
UNIFORM = {NO_DIFF.name}

#: which view answers which question by default. Q1 is an environment question
#: and uses the HBEC view; Q2 and Q3 feed test generation and use the BRTG view
#: the caller selected, so one mine run can serve both tasks without the test
#: generator ever seeing what the HBEC view was allowed to read.
QUESTION_VIEW = {"environment": "hbec",
                 "failure": None,        # the caller's BRTG view
                 "characteristics": None}


def filter_chunks(chunks: list, view: View) -> list:
    """Only the chunks this view may index. Nothing downstream can re-admit the
    rest: they are absent from the collection, not merely unranked."""
    return [c for c in chunks
            if view.keeps((c.metadata or {}).get("source_type", ""))]


def view_for(question_id: str, brtg_view: View) -> View:
    """The view a question is answered in — the single decision point.

    The environment question normally escapes to HBEC, because the environment is
    not the thing being predicted. Under a view that withholds the diffs outright
    there is nothing to escape to, so every question is answered in that one view.
    Repair re-asks and the escalation ladder route through here too, which is what
    keeps a second call from re-admitting what the first was denied.
    """
    if brtg_view.name in UNIFORM:
        return brtg_view
    name = QUESTION_VIEW.get(question_id)
    return HBEC if name == "hbec" else brtg_view


def allowed_sources(view: View, sources: tuple) -> tuple:
    """A question's source list, narrowed to what this view actually holds.

    A balanced query divides its slots between the source types it is given; a
    type absent from the collection would take a share and return nothing, so
    narrowing here buys back retrieval depth rather than merely tidying.
    """
    kept = tuple(s for s in sources if view.keeps(s))
    return kept or tuple(view.allowed)


def provenance_ok(view: View, source_type: str) -> bool:
    """A last assertion before a fact is written into the context: a value whose
    provenance is a withheld source must never appear, whatever the model did."""
    return source_type not in view.withheld

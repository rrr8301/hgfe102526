"""The compact structured context, and the provenance that travels with it (DP4, DP5).

`NumericalContext` is what the Strategy Generator reads instead of the PR body,
the issue thread and the diff. Two rules shape it:

* a field the evidence does not state stays **null** — a miner that guesses a
  version number or a tolerance has invented the very thing the reproduction
  will be judged against;
* every non-null fact carries its provenance, so a reader (and the isolation
  check in `views`) can tell which artifact it came from.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class Fact:
    """One extracted value and where it came from."""
    field: str
    value: object
    source: str = ""                 # chunk source_type: issue, diff, readme, …
    evidence_location: str = ""      # file path / comment index / hunk range

    def as_dict(self) -> dict:
        return {"field": self.field, "value": self.value,
                "source": self.source, "evidence_location": self.evidence_location}


def _env() -> dict:
    return {"language": None, "language_version": None, "dependencies": [],
            "installation_commands": [], "build_commands": [], "test_commands": [],
            "system_packages": [], "era_constraints": [], "numerical_backend": None}


def _failure() -> dict:
    return {"category": None, "affected_api": None, "observed_behavior": None,
            "expected_behavior": None, "manifestation": None,
            "trigger_conditions": [], "input_output_examples": [], "oracle_clues": []}


def _characteristics() -> dict:
    return {"dtype": None, "precision": None, "magnitude": None, "tolerance": None,
            "category_specific_evidence": [], "reproduction_risks": []}


@dataclass
class NumericalContext:
    """`C = RepoMiner(D)`, with |C| << |D|."""
    repo: str = ""
    pr_number: int = 0
    reference: str = ""
    buggy_commit: str = ""
    fixed_commit: str = ""
    pr_date: str = ""
    category: str = ""               # ANNOTATED, never inferred here
    view: str = ""                   # which evidence view produced this
    withheld: list = field(default_factory=list)

    environment: dict = field(default_factory=_env)
    numerical_failure: dict = field(default_factory=_failure)
    numerical_characteristics: dict = field(default_factory=_characteristics)

    provenance: list = field(default_factory=list)      # [Fact.as_dict(), …]
    retrieval: dict = field(default_factory=dict)       # per question: hits, refs
    usage: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    expansions: list = field(default_factory=list)      # DP7, appended by expand.py

    # ---- construction ----------------------------------------------------- #
    def record(self, field_name: str, value, source: str = "",
               location: str = "") -> None:
        """Write a fact and its provenance. Empty values are dropped rather than
        stored as evidence of nothing."""
        if value in (None, "", [], {}):
            return
        self.provenance.append(
            Fact(field_name, value, source, location).as_dict())

    # ---- what it costs the prompt ----------------------------------------- #
    def compression(self, raw_chars: int) -> dict:
        """|C| against |D| — the DP4 number, measured rather than asserted."""
        compact = len(self.to_json())
        return {"raw_chars": raw_chars, "context_chars": compact,
                "ratio": round(compact / raw_chars, 4) if raw_chars else None}

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    # ---- what the Strategy Generator actually reads ------------------------ #
    def to_prompt(self) -> str:
        """The context as the compact block a prompt carries: the three sections,
        nulls omitted, provenance summarised rather than enumerated."""
        def section(name: str, data: dict) -> str:
            kept = {k: v for k, v in data.items() if v not in (None, "", [], {})}
            return f"{name}:\n{json.dumps(kept, indent=1, default=str)}" if kept else ""

        head = (f"INSTANCE: {self.repo}#{self.pr_number}  ({self.reference})\n"
                f"CATEGORY (annotated): {self.category or 'unknown'}\n"
                f"BUGGY COMMIT: {self.buggy_commit[:12]}   PR DATE: {self.pr_date}")
        body = [section("ENVIRONMENT", self.environment),
                section("NUMERICAL FAILURE", self.numerical_failure),
                section("NUMERICAL CHARACTERISTICS", self.numerical_characteristics)]
        tail = ""
        if self.withheld:
            tail = ("\nEVIDENCE WITHHELD FROM THIS CONTEXT: "
                    + ", ".join(self.withheld)
                    + " — do not assume facts about them.")
        return "\n\n".join([head] + [b for b in body if b]) + tail

    @classmethod
    def from_dict(cls, d: dict) -> "NumericalContext":
        known = {f for f in cls.__dataclass_fields__}       # noqa: no-member
        return cls(**{k: v for k, v in d.items() if k in known})

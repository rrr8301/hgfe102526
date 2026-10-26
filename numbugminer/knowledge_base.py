"""Load the labeled seed corpus that backs retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Example:
    id: str
    label: str  # "numerical_bug" | "not_numerical_bug"
    bug_type: str
    snippet: str
    description: str
    source: str = "seed"

    @property
    def is_numerical(self) -> bool:
        return self.label == "numerical_bug"

    def embed_text(self) -> str:
        # What we embed for retrieval: the raw log snippet plus its description.
        return f"{self.snippet}\n{self.description}"


def load_corpus(path: Path) -> list[Example]:
    examples: list[Example] = []
    with open(path, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no}: bad JSON ({e})") from e
            examples.append(
                Example(
                    id=d["id"],
                    label=d["label"],
                    bug_type=d.get("bug_type", "other"),
                    snippet=d["snippet"],
                    description=d.get("description", ""),
                    source=d.get("source", "seed"),
                )
            )
    if not examples:
        raise ValueError(f"Seed corpus {path} is empty.")
    return examples

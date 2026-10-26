"""Run the RAG-free keyword filter over SWE-bench data.

SWE-bench ships GitHub issue+PR tasks (``problem_statement``, ``hints_text``,
the fix ``patch`` …) but carries *no* numerical-bug label, so this is a
discovery / prescreen pass, not a labelled precision/recall benchmark: we scan
each instance's text with :func:`numbugminer.keyword_filter.classify` and report
which instances trip the filter, with what keywords, at what confidence. It's
the same cheap offline classifier the miner uses to prescreen a repo's PRs,
pointed at a standard corpus so its firing behaviour is easy to eyeball.

Loading is dependency-light on purpose:

* a local ``.jsonl`` file (one instance per line) — no third-party deps;
* a local ``.parquet`` file (the format SWE-bench ships on the HF hub) — needs
  ``pyarrow`` (``pip install -e '.[swebench]'``);
* a HuggingFace dataset id (e.g. ``princeton-nlp/SWE-bench_Lite``) — pulled
  over the datasets-server JSON API with the stdlib, no ``datasets`` package.
  This endpoint is occasionally unavailable; on failure we point you at the
  parquet download so a run is always possible offline.

Everything below the loader is plain data, so a batch harness can
``from numbugminer.swebench_eval import load_instances, evaluate, summarize``
and drive it directly.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from . import keyword_filter
from .keyword_filter import DEFAULT_THRESHOLD

# The instance text fields the filter reads by default. problem_statement is the
# GitHub issue; hints_text is the surrounding discussion. Both are prose. The
# fix `patch` is code — including it lures word-boundary matches onto identifier
# names (a variable literally called `nan`), so it's opt-in via --fields.
DEFAULT_FIELDS: tuple[str, ...] = ("problem_statement", "hints_text")

# Every text field SWE-bench instances carry that's worth scanning.
TEXT_FIELDS: tuple[str, ...] = ("problem_statement", "hints_text", "patch", "test_patch")

_HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
_HF_PAGE = 100  # datasets-server caps a /rows page at 100


@dataclass(frozen=True)
class SweInstance:
    """One SWE-bench task, trimmed to what the keyword filter needs."""

    instance_id: str
    repo: str
    problem_statement: str = ""
    hints_text: str = ""
    patch: str = ""
    test_patch: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "SweInstance":
        def s(key: str) -> str:
            v = row.get(key)
            return v if isinstance(v, str) else ("" if v is None else str(v))

        # Field fallbacks for SWE-bench-like corpora that name things differently
        # (e.g. R2E-Gym uses repo_name + commit_hash and has no instance_id).
        repo = s("repo") or s("repo_name")
        commit = s("commit_hash") or s("base_commit")
        instance_id = s("instance_id") or (
            f"{repo}__{commit[:12]}" if repo and commit else (repo or "<unknown>"))
        return cls(
            instance_id=instance_id,
            repo=repo,
            problem_statement=s("problem_statement"),
            hints_text=s("hints_text"),
            patch=s("patch"),
            test_patch=s("test_patch"),
        )

    def text(self, fields: Sequence[str] = DEFAULT_FIELDS) -> str:
        """The concatenated text of `fields`, blank-line separated."""
        parts = [getattr(self, f, "") for f in fields]
        return "\n\n".join(p for p in parts if p)


# --- loading -----------------------------------------------------------------


def _load_jsonl(path: Path) -> Iterator[SweInstance]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield SweInstance.from_row(json.loads(line))


def _load_parquet(path: Path) -> Iterator[SweInstance]:
    try:
        import pyarrow.parquet as pq
    except ImportError as e:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "reading a .parquet SWE-bench file needs pyarrow: "
            "pip install -e '.[swebench]'  (or point at a .jsonl instead)"
        ) from e
    table = pq.read_table(path, columns=None)
    for row in table.to_pylist():
        yield SweInstance.from_row(row)


def _load_hf(dataset: str, config: str, split: str,
             limit: int | None, offset: int) -> Iterator[SweInstance]:
    """Stream rows from the HuggingFace datasets-server JSON API (stdlib only)."""
    fetched = 0
    while True:
        page = min(_HF_PAGE, (limit - fetched) if limit is not None else _HF_PAGE)
        if page <= 0:
            return
        url = (f"{_HF_ROWS_URL}?dataset={urllib.parse.quote(dataset)}"
               f"&config={urllib.parse.quote(config)}&split={urllib.parse.quote(split)}"
               f"&offset={offset + fetched}&length={page}")
        rows = _hf_get(url).get("rows", [])
        if not rows:
            return
        for r in rows:
            yield SweInstance.from_row(r.get("row", {}))
        fetched += len(rows)
        if len(rows) < page:
            return


def _hf_get(url: str, tries: int = 4) -> dict:
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "numbugminer"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:  # 503 while HF warms the dataset
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            break
        except urllib.error.URLError as e:
            last = e
            break
    raise RuntimeError(
        f"could not reach the HuggingFace datasets-server ({last}). "
        "It warms datasets on demand and is sometimes briefly unavailable. "
        "Download the parquet and pass it as a path instead, e.g.\n"
        "  curl -L -o swe.parquet "
        "https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/"
        "resolve/main/data/test-00000-of-00001.parquet\n"
        "  numbugminer swebench swe.parquet"
    )


def load_instances(source: str, *, config: str = "default", split: str = "test",
                   limit: int | None = None, offset: int = 0) -> list[SweInstance]:
    """Load SWE-bench instances from a local file or a HF dataset id.

    `source` is a path to a ``.jsonl``/``.parquet`` file, or a HuggingFace
    dataset id (``owner/name``). `limit`/`offset` slice the stream.
    """
    path = Path(source)
    if path.exists():
        if path.suffix == ".parquet":
            stream: Iterable[SweInstance] = _load_parquet(path)
        elif path.suffix in (".jsonl", ".json"):
            stream = _load_jsonl(path)
        else:
            raise ValueError(f"unsupported file type {path.suffix!r}; use .jsonl or .parquet")
        sliced = _slice(stream, limit, offset)
    else:
        # A HF dataset id paginates server-side, so offset/limit go to the API.
        sliced = _load_hf(source, config, split, limit, offset)
    return list(sliced)


def _slice(stream: Iterable[SweInstance], limit: int | None, offset: int) -> Iterator[SweInstance]:
    for i, inst in enumerate(stream):
        if i < offset:
            continue
        if limit is not None and (i - offset) >= limit:
            return
        yield inst


# --- evaluation --------------------------------------------------------------


@dataclass(frozen=True)
class InstanceResult:
    instance_id: str
    repo: str
    is_numerical_bug: bool
    bug_type: str
    confidence: float
    score: float
    keywords: list[str]
    explanation: str
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "is_numerical_bug": self.is_numerical_bug,
            "bug_type": self.bug_type,
            "confidence": self.confidence,
            "score": self.score,
            "keywords": self.keywords,
            "explanation": self.explanation,
            "evidence": self.evidence,
        }


def evaluate(instances: Iterable[SweInstance], *,
             fields: Sequence[str] = DEFAULT_FIELDS,
             threshold: float = DEFAULT_THRESHOLD) -> list[InstanceResult]:
    """Run the keyword filter over every instance's text."""
    results: list[InstanceResult] = []
    for inst in instances:
        text = inst.text(fields)
        hits = keyword_filter.scan(text)
        verdict = keyword_filter.classify(text, threshold=threshold)
        results.append(InstanceResult(
            instance_id=inst.instance_id,
            repo=inst.repo,
            is_numerical_bug=verdict.is_numerical_bug,
            bug_type=verdict.bug_type,
            confidence=verdict.confidence,
            score=round(sum(h.weight for h in hits), 3),
            keywords=[h.keyword for h in hits],
            explanation=verdict.explanation,
            evidence=[{"line": e.line, "reason": e.reason} for e in verdict.evidence],
        ))
    return results


def summarize(results: Sequence[InstanceResult]) -> dict:
    """Aggregate stats over a run: firing rate, bug-type + keyword frequencies."""
    total = len(results)
    flagged = [r for r in results if r.is_numerical_bug]
    bug_types = Counter(r.bug_type for r in flagged)
    keyword_freq = Counter(kw for r in results for kw in r.keywords)
    by_repo = Counter(r.repo for r in flagged)
    return {
        "total": total,
        "flagged": len(flagged),
        "flag_rate": round(len(flagged) / total, 4) if total else 0.0,
        "bug_types": dict(bug_types.most_common()),
        "keyword_freq": dict(keyword_freq.most_common()),
        "flagged_by_repo": dict(by_repo.most_common()),
    }

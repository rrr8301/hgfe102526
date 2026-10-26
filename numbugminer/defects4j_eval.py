from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import keyword_filter
from .keyword_filter import DEFAULT_THRESHOLD

# The 17 active Defects4J projects. Math (Apache Commons Math) is the most
# numerically dense, so it's the natural default target.
ALL_PROJECTS = (
    "Chart", "Cli", "Closure", "Codec", "Collections", "Compress", "Csv", "Gson",
    "JacksonCore", "JacksonDatabind", "JacksonXml", "Jsoup", "JxPath", "Lang",
    "Math", "Mockito", "Time",
)
DEFAULT_FIELDS: tuple[str, ...] = ("trigger_tests", "modified_classes")
TEXT_FIELDS: tuple[str, ...] = ("trigger_tests", "modified_classes", "report_id")

_RAW = "https://raw.githubusercontent.com/rjust/defects4j/master/framework/projects"


@dataclass(frozen=True)
class D4JBug:
    project: str
    bug_id: str
    report_id: str = ""
    report_url: str = ""
    revision_buggy: str = ""
    revision_fixed: str = ""
    trigger_tests: str = ""     # failing-test stack trace(s)
    modified_classes: str = ""  # fully-qualified changed class names

    @property
    def key(self) -> str:
        return f"{self.project}-{self.bug_id}"

    def text(self, fields: Sequence[str] = DEFAULT_FIELDS) -> str:
        parts = [getattr(self, f, "") for f in fields]
        return "\n\n".join(p for p in parts if p)


# --- loading -----------------------------------------------------------------


def _http_get(url: str, tries: int = 3) -> str | None:
    """Fetch a text file; return None on 404, raise on persistent transport error."""
    last: Exception | None = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "numbugminer"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            last = e
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
        except urllib.error.URLError as e:
            last = e
            if attempt < tries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url}: {last}")


def _parse_active_bugs(csv_text: str) -> list[dict]:
    """Parse active-bugs.csv -> [{bug.id, revision.id.buggy, ...}]."""
    lines = [ln for ln in (csv_text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split(",")]
    rows = []
    for ln in lines[1:]:
        cells = ln.split(",")
        if len(cells) >= len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def _bug_from_row(project: str, row: dict, trigger: str, modified: str) -> D4JBug:
    return D4JBug(
        project=project,
        bug_id=row.get("bug.id", ""),
        report_id=row.get("report.id", ""),
        report_url=row.get("report.url", ""),
        revision_buggy=row.get("revision.id.buggy", ""),
        revision_fixed=row.get("revision.id.fixed", ""),
        trigger_tests=trigger or "",
        modified_classes=modified or "",
    )


def _load_github(projects: Sequence[str], limit: int | None,
                 progress=None) -> list[D4JBug]:
    out: list[D4JBug] = []
    for project in projects:
        csv_text = _http_get(f"{_RAW}/{project}/active-bugs.csv")
        rows = _parse_active_bugs(csv_text or "")
        if progress:
            progress(f"{project}: {len(rows)} active bugs")
        for row in rows:
            if limit is not None and len(out) >= limit:
                return out
            bid = row.get("bug.id", "")
            trig = _http_get(f"{_RAW}/{project}/trigger_tests/{bid}") or ""
            mod = _http_get(f"{_RAW}/{project}/modified_classes/{bid}.src") or ""
            out.append(_bug_from_row(project, row, trig, mod))
    return out


def _load_local(root: Path, projects: Sequence[str], limit: int | None) -> list[D4JBug]:
    # Accept either the defects4j root or its framework/projects dir.
    proj_dir = root / "framework" / "projects"
    if not proj_dir.is_dir():
        proj_dir = root
    out: list[D4JBug] = []
    for project in projects:
        pdir = proj_dir / project
        csv = pdir / "active-bugs.csv"
        if not csv.is_file():
            continue
        for row in _parse_active_bugs(csv.read_text(encoding="utf-8", errors="replace")):
            if limit is not None and len(out) >= limit:
                return out
            bid = row.get("bug.id", "")

            def _read(rel: str) -> str:
                p = pdir / rel
                return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""

            out.append(_bug_from_row(project, row,
                                     _read(f"trigger_tests/{bid}"),
                                     _read(f"modified_classes/{bid}.src")))
    return out


def _load_jsonl(path: Path, limit: int | None) -> list[D4JBug]:
    out: list[D4JBug] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if limit is not None and len(out) >= limit:
                break
            row = json.loads(line)
            out.append(D4JBug(
                project=row.get("project", ""), bug_id=str(row.get("bug_id", "")),
                report_id=row.get("report_id", ""), report_url=row.get("report_url", ""),
                revision_buggy=row.get("revision_buggy", ""),
                revision_fixed=row.get("revision_fixed", ""),
                trigger_tests=row.get("trigger_tests", ""),
                modified_classes=row.get("modified_classes", "")))
    return out


def load_bugs(source: str | None = None, *, projects: Sequence[str] | None = None,
              limit: int | None = None, progress=None) -> list[D4JBug]:
    """Load Defects4J bugs.

    `source`: a ``.jsonl`` file, a local Defects4J checkout dir, or None/"github"
    to fetch from rjust/defects4j. `projects` limits which projects to load
    (default: all 17).
    """
    projects = tuple(projects) if projects else ALL_PROJECTS
    if source and source not in ("github", "gh"):
        path = Path(source)
        if path.is_file() and path.suffix in (".jsonl", ".json"):
            return _load_jsonl(path, limit)
        if path.is_dir():
            return _load_local(path, projects, limit)
        raise ValueError(f"source {source!r} is neither a .jsonl file, a Defects4J "
                         "checkout dir, nor 'github'")
    return _load_github(projects, limit, progress=progress)


# --- evaluation (mirrors swebench_eval) --------------------------------------


@dataclass(frozen=True)
class BugResult:
    key: str
    project: str
    bug_id: str
    report_id: str
    is_numerical_bug: bool
    bug_type: str
    confidence: float
    score: float
    keywords: list[str]
    explanation: str
    evidence: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "project": self.project, "bug_id": self.bug_id,
            "report_id": self.report_id, "is_numerical_bug": self.is_numerical_bug,
            "bug_type": self.bug_type, "confidence": self.confidence, "score": self.score,
            "keywords": self.keywords, "explanation": self.explanation,
            "evidence": self.evidence,
        }


def evaluate(bugs: Iterable[D4JBug], *, fields: Sequence[str] = DEFAULT_FIELDS,
             threshold: float = DEFAULT_THRESHOLD) -> list[BugResult]:
    results: list[BugResult] = []
    for bug in bugs:
        text = bug.text(fields)
        hits = keyword_filter.scan(text)
        v = keyword_filter.classify(text, threshold=threshold)
        results.append(BugResult(
            key=bug.key, project=bug.project, bug_id=bug.bug_id, report_id=bug.report_id,
            is_numerical_bug=v.is_numerical_bug, bug_type=v.bug_type, confidence=v.confidence,
            score=round(sum(h.weight for h in hits), 3),
            keywords=[h.keyword for h in hits], explanation=v.explanation,
            evidence=[{"line": e.line, "reason": e.reason} for e in v.evidence]))
    return results


def summarize(results: Sequence[BugResult]) -> dict:
    total = len(results)
    flagged = [r for r in results if r.is_numerical_bug]
    by_project_total = Counter(r.project for r in results)
    by_project_flagged = Counter(r.project for r in flagged)
    return {
        "total": total,
        "flagged": len(flagged),
        "flag_rate": round(len(flagged) / total, 4) if total else 0.0,
        "bug_types": dict(Counter(r.bug_type for r in flagged).most_common()),
        "keyword_freq": dict(Counter(k for r in results for k in r.keywords).most_common()),
        "by_project": {p: {"total": by_project_total[p], "flagged": by_project_flagged.get(p, 0)}
                       for p in sorted(by_project_total)},
    }

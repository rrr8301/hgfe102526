from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

_KEEP = re.compile(
    r"^(?:E\s|FAILED |ERROR |=+ .*(?:failed|passed|error).* =+|"
    r"\s*(?:Max (?:absolute|relative) difference|Mismatched elements|"
    r"ACTUAL:|DESIRED:|assert |AssertionError|"
    r"ModuleNotFoundError|ImportError|error:|fatal error:|"
    r"ERROR: (?:Could not|No matching|Cannot)|"
    r"note: This error originates))", re.M)
_NOISE = re.compile(r"^\s*(?:Requirement already satisfied|Using cached|"
                    r"Downloading|Collecting|Installing collected|"
                    r"Preparing metadata|Building wheel|warnings\.warn)", re.I)


def compact_log(output: str, *, max_lines: int = 12, width: int = 220) -> str:
    lines = [l for l in (output or "").splitlines() if l.strip() and not _NOISE.match(l)]
    kept = [l[:width] for l in lines if _KEEP.match(l)]
    if len(kept) < max_lines:
        tail = [l[:width] for l in lines[-(max_lines - len(kept)):]]
        kept = kept + [t for t in tail if t not in kept]
    return "\n".join(kept[:max_lines])


@dataclass
class Attempt:
    iteration: int
    change: str
    outcome: str
    diagnosis: str = ""
    evidence_used: list = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in ("", [], None)}


@dataclass
class RunState:
    task: str = "BRTG"
    iteration: int = 0
    current_outcome: str = ""
    last_change: str = ""
    diagnosis: str = ""
    missing_evidence: str = ""
    next_action: str = ""
    attempts: list = field(default_factory=list)
    attempted_versions: dict = field(default_factory=dict)
    attempted_configs: list = field(default_factory=list)
    evidence_requests: list = field(default_factory=list)
    budget: dict = field(default_factory=dict)

    def record(self, *, change: str, outcome: str, diagnosis: str = "",
               evidence_used: list | None = None, detail: str = "") -> None:
        self.iteration += 1
        self.current_outcome = outcome
        self.last_change = change
        self.diagnosis = diagnosis
        self.attempts.append(Attempt(self.iteration, change, outcome, diagnosis,
                                     evidence_used or [], detail).as_dict())

    def note_version(self, package: str, version: str) -> None:
        tried = self.attempted_versions.setdefault(package, [])
        if version and version not in tried:
            tried.append(version)

    def note_config(self, config: str) -> None:
        if config and config not in self.attempted_configs:
            self.attempted_configs.append(config)

    def already_tried(self, package: str, version: str) -> bool:
        return version in self.attempted_versions.get(package, [])

    def feedback(self, *, verdict_dict: dict | None = None,
                 log_excerpt: str = "") -> dict:
        block = {
            "iteration": self.iteration,
            "task": self.task,
            "current_outcome": self.current_outcome,
            "last_change": self.last_change,
            "diagnosis": self.diagnosis,
        }
        if self.attempted_versions:
            block["attempted_versions"] = self.attempted_versions
        if self.attempted_configs:
            block["attempted_configs"] = self.attempted_configs[-6:]
        if self.missing_evidence:
            block["missing_evidence"] = self.missing_evidence
        if self.next_action:
            block["next_action"] = self.next_action
        if verdict_dict:
            block["verdict"] = verdict_dict
        if log_excerpt:
            block["log_excerpt"] = log_excerpt
        if self.attempts[:-1]:
            block["earlier_attempts"] = [
                {k: a[k] for k in ("iteration", "change", "outcome") if k in a}
                for a in self.attempts[:-1]][-4:]
        return block

    def as_dict(self) -> dict:
        return asdict(self)

from __future__ import annotations

import re
from dataclasses import dataclass

# Word-boundary patterns keep "inf" from matching "information", etc.
_SIGNAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("nan", re.compile(r"\bnan\b", re.I)),
    ("inf", re.compile(r"[-+]?\binf(inity)?\b", re.I)),
    ("overflow", re.compile(r"\boverflow\b", re.I)),
    ("underflow", re.compile(r"\bunderflow\b", re.I)),
    ("divide_by_zero", re.compile(r"divi(sion|de) by zero|ZeroDivisionError", re.I)),
    ("invalid_value", re.compile(r"invalid value encountered", re.I)),
    ("floating_point", re.compile(r"FloatingPointError|floating[- ]point exception", re.I)),
    ("not_finite", re.compile(r"\bnon[- ]?finite\b|not finite|isfinite", re.I)),
    ("loss_nan", re.compile(r"(loss|grad(ient)?)\D{0,20}\bnan\b|\bnan\b\D{0,20}(loss|grad)", re.I)),
    ("linalg", re.compile(r"LinAlgError|singular matrix|did not converge|convergence", re.I)),
]

_GENERIC_ERROR = re.compile(
    r"Traceback \(most recent call last\)|^\s*Error:|Exception|assert|FAILED|"
    r"error:|panic:|Segmentation fault",
    re.I,
)


@dataclass
class Region:
    start: int  # 1-based line number of the first line
    lines: list[str]
    signals: set[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _classify_line(line: str) -> set[str]:
    return {name for name, pat in _SIGNAL_PATTERNS if pat.search(line)}


def scan_signals(text: str) -> set[str]:
    """Numerical-signal names present anywhere in an arbitrary block of text.

    Used on non-log evidence (PR body, diff, comments, issues) where the
    line-window machinery of `extract_regions` isn't needed.
    """
    return {name for name, pat in _SIGNAL_PATTERNS if pat.search(text or "")}


def extract_regions(log: str, context: int = 4, max_regions: int = 40) -> list[Region]:
    """Return merged windows around each numerical-signal (or error) line."""
    lines = log.splitlines()
    hits: list[tuple[int, set[str]]] = []
    for i, line in enumerate(lines):
        sig = _classify_line(line)
        if sig:
            hits.append((i, sig))
        elif _GENERIC_ERROR.search(line):
            hits.append((i, set()))

    if not hits:
        return []

    # Merge overlapping context windows.
    regions: list[Region] = []
    cur_start = max(0, hits[0][0] - context)
    cur_end = min(len(lines), hits[0][0] + context + 1)
    cur_sig: set[str] = set(hits[0][1])
    for idx, sig in hits[1:]:
        w_start = max(0, idx - context)
        w_end = min(len(lines), idx + context + 1)
        if w_start <= cur_end:
            cur_end = max(cur_end, w_end)
            cur_sig |= sig
        else:
            regions.append(Region(cur_start + 1, lines[cur_start:cur_end], cur_sig))
            cur_start, cur_end, cur_sig = w_start, w_end, set(sig)
    regions.append(Region(cur_start + 1, lines[cur_start:cur_end], cur_sig))

    # Numerical-signal regions first, then by size; cap the count.
    regions.sort(key=lambda r: (not r.signals, -len(r.lines)))
    return regions[:max_regions]


def build_excerpt(regions: list[Region], max_chars: int) -> str:
    """Concatenate regions into a single excerpt within the char budget."""
    chunks: list[str] = []
    total = 0
    for r in regions:
        header = f"--- log lines {r.start}-{r.start + len(r.lines) - 1}"
        if r.signals:
            header += f"  [signals: {', '.join(sorted(r.signals))}]"
        block = f"{header} ---\n{r.text}"
        if total + len(block) > max_chars and chunks:
            break
        chunks.append(block)
        total += len(block)
    return "\n\n".join(chunks)


def numerical_signals(regions: list[Region]) -> set[str]:
    out: set[str] = set()
    for r in regions:
        out |= r.signals
    return out

"""Render a RunReport as a human summary or JSON."""
from __future__ import annotations

import json

from .models import RunReport, StepResult, to_dict

_CHECK = "✓"
_CROSS = "✗"
_DASH = "–"


def _mark(step: StepResult) -> str:
    if step is None:
        return _DASH
    if step.skipped:
        return _DASH
    return _CHECK if step.success else _CROSS


def _step_line(label: str, step: StepResult) -> str:
    if step is None:
        return f"  {_DASH} {label}: not run"
    status = "skipped" if step.skipped else ("ok" if step.success else "FAILED")
    extra = ""
    if step.tests_passed is not None or step.tests_failed is not None:
        extra = f" ({step.tests_passed or 0} passed, {step.tests_failed or 0} failed)"
    dur = sum(c.duration_s for c in step.commands)
    return f"  {_mark(step)} {label}: {status}{extra} — {step.summary} [{dur:.1f}s]"


def summary(report: RunReport) -> str:
    d = report.detect
    lines = [
        f"Repo: {report.repo_path}",
        f"  Detected: {d.language.value} / {d.build_system.value} "
        f"(confidence {d.confidence})",
    ]
    if d.evidence:
        lines.append(f"    evidence: {'; '.join(d.evidence)}")
    if d.missing_tools:
        lines.append(f"    ⚠ missing tools: {', '.join(d.missing_tools)}")
    lines.append(_step_line("build", report.build))
    lines.append(_step_line("test", report.test))
    verdict = "PASS" if report.success else "FAIL"
    lines.append(f"  => {verdict}")
    return "\n".join(lines)


def as_json(report: RunReport, indent: int = 2) -> str:
    return json.dumps(to_dict(report), indent=indent)

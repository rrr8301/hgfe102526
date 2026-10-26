from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import preprocess
from .pr_source import PRBundle

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _clean(text: str) -> str:
    """Drop PR/issue template boilerplate (HTML comments) and collapse blanks."""
    text = _HTML_COMMENT.sub("", text or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@dataclass
class BugTask:
    source_url: str
    repo_url: str
    buggy_commit: str
    fixed_commit: str
    problem_statement: str
    signals: list[str] = field(default_factory=list)
    # The PR's test-file hunks ONLY (not the fix). Applied to the buggy checkout
    # during the agent's preprocessing so the reproducing test is present and
    # failing before the agent starts — the fail->pass target.
    test_patch: str = ""
    test_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _trim(text: str, n: int) -> str:
    text = _clean(text)
    return text if len(text) <= n else text[:n] + " …[trimmed]"


def _signal_comments(bodies: list[str], label: str, per_cap: int, limit: int) -> list[str]:
    out = []
    for b in bodies:
        if preprocess.scan_signals(b):
            out.append(f"[{label}] {_trim(b, per_cap)}")
        if len(out) >= limit:
            break
    return out


def problem_statement(
    bundle: PRBundle,
    *,
    include_pr_body: bool = True,
    include_discussion: bool = True,
    include_commits: bool = False,
    include_diff: bool = False,
    max_chars: int = 8000,
) -> str:
    """Assemble the agent-facing bug report from issue + PR data.

    By default this is the *problem* (issue report + PR description + symptom
    discussion), NOT the solution: the diff and fix commit messages are left
    out so the agent must repair the bug on its own.
    """
    parts: list[str] = []
    r = bundle.ref
    parts.append(f"# Numerical bug reported in {r.owner}/{r.repo} (PR #{r.number})")
    if bundle.title:
        parts.append(f"PR title: {bundle.title}")

    # The linked issue IS the bug report — the primary problem statement.
    for li in bundle.linked_issues:
        lab = f" [labels: {', '.join(li.labels)}]" if li.labels else ""
        block = f"\n## Reported issue #{li.number} ({li.relation}){lab}\n" \
                f"Title: {li.title}\n{_trim(li.body, 2500)}"
        sig = _signal_comments(li.comments, f"issue#{li.number}", 1000, 3)
        if sig:
            block += "\n" + "\n".join(sig)
        parts.append(block)

    if include_pr_body and bundle.body.strip():
        parts.append(f"\n## PR description\n{_trim(bundle.body, 2500)}")

    if include_discussion:
        convo = (
            _signal_comments(bundle.issue_comments, "comment", 900, 4)
            + _signal_comments(bundle.review_bodies, "review", 900, 3)
            + [f"[inline {c['path']}] {_trim(c['body'], 700)}"
               for c in bundle.review_comments
               if preprocess.scan_signals(c["body"] + " " + c.get("diff_hunk", ""))][:4]
        )
        if convo:
            parts.append("\n## Discussion (numerical mentions)\n" + "\n".join(convo))

    if include_commits and bundle.commit_messages:
        parts.append("\n## Fix commit messages\n"
                     + "\n\n".join(bundle.commit_messages))

    if include_diff and bundle.diff:
        parts.append("\n## Fix diff (SOLUTION — leaks the answer)\n" + bundle.diff)

    signals = sorted(preprocess.scan_signals("\n".join(parts)))
    if signals:
        parts.append(f"\nNumerical signals detected in the report: {', '.join(signals)}.")

    # Changed files help the agent locate the bug without revealing the fix.
    if bundle.files:
        listing = "\n".join(f"- {f['filename']}" for f in bundle.files[:30])
        parts.append(f"\n## Files touched by the fix (locations only)\n{listing}")

    return _trim("\n".join(parts), max_chars)


def build_bug_task(
    bundle: PRBundle,
    *,
    signals: list[str] | None = None,
    **ps_kwargs,
) -> BugTask:
    r = bundle.ref
    stmt = problem_statement(bundle, **ps_kwargs)
    if signals is None:
        signals = sorted(preprocess.scan_signals(stmt))
    from .repo import extract_test_patch  # test-only hunks (the reproducing test)

    test_patch, test_files = extract_test_patch(bundle.diff)
    return BugTask(
        source_url=r.html_url,
        repo_url=f"https://github.com/{r.owner}/{r.repo}.git",
        buggy_commit=bundle.buggy_commit,
        fixed_commit=bundle.fixed_commit,
        problem_statement=stmt,
        signals=signals,
        test_patch=test_patch,
        test_files=test_files,
    )

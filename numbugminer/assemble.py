from __future__ import annotations

import re

from . import preprocess
from .pr_source import PRBundle

# Tokens that mark a diff hunk as numerically interesting even without an
# explicit NaN/Inf word: math ops, precision knobs, linear-algebra calls.
_MATH_HUNK = re.compile(
    r"\b(sqrt|exp|log|log1p|expm1|pow|sin|cos|tan|arc|sinh|cosh|tanh|"
    r"mean|std|var|norm|dot|matmul|inv|solve|svd|eig|cholesky|lstsq|det|"
    r"softmax|sigmoid|logit|logsumexp|clip|clamp|eps|epsilon|tol|tolerance|"
    r"atol|rtol|precision|float\d*|double|float32|float64|nan|inf|isfinite|"
    r"divide|denom|/\s*0|1e-|1e\+|overflow|underflow|round|ulp|cond)\b",
    re.I,
)

# Section budget weights (fraction of max_chars) — soft caps, packed in order.
_TRIM = {"body": 3000, "issue_body": 2000, "commits": 3000, "comment": 1200}


def _trim(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + " …[trimmed]"


def _split_hunks(patch: str) -> list[str]:
    """Split a unified-diff file patch into individual @@ hunks."""
    if not patch:
        return []
    hunks: list[str] = []
    cur: list[str] = []
    for line in patch.splitlines():
        if line.startswith("@@"):
            if cur:
                hunks.append("\n".join(cur))
            cur = [line]
        else:
            cur.append(line)
    if cur:
        hunks.append("\n".join(cur))
    return hunks


def _relevant_hunks(files: list[dict], budget: int) -> tuple[list[str], set[str]]:
    """Keep diff hunks that carry a numerical signal or touch math code."""
    blocks: list[str] = []
    signals: set[str] = set()
    total = 0
    for f in files:
        for hunk in _split_hunks(f.get("patch", "")):
            sig = preprocess.scan_signals(hunk)
            if not sig and not _MATH_HUNK.search(hunk):
                continue
            signals |= sig
            block = f"--- {f['filename']} ---\n{hunk}"
            if total + len(block) > budget and blocks:
                return blocks, signals
            blocks.append(block)
            total += len(block)
    return blocks, signals


def _signal_snippets(bodies: list[str], label: str, per_cap: int) -> list[str]:
    """Keep only comment/review bodies that mention a numerical signal."""
    out: list[str] = []
    for b in bodies:
        if preprocess.scan_signals(b):
            out.append(f"[{label}] {_trim(b, per_cap)}")
    return out


def assemble_evidence(bundle: PRBundle, max_chars: int) -> tuple[str, list[str], str]:
    """Return (excerpt, sorted signals, retrieval query)."""
    signals: set[str] = set()
    sections: list[str] = []  # packed in priority order

    def add(text: str) -> bool:
        """Append a section if it fits the running budget; return whether it fit."""
        used = sum(len(s) for s in sections)
        if used + len(text) > max_chars and sections:
            return False
        sections.append(text)
        return True

    # 1. PR header (always) --------------------------------------------------
    labels = f" | labels: {', '.join(bundle.labels)}" if bundle.labels else ""
    merged = "merged" if bundle.merged else bundle.state
    add(
        f"# PULL REQUEST {bundle.ref.owner}/{bundle.ref.repo}#{bundle.ref.number} "
        f"({merged}){labels}\n"
        f"TITLE: {bundle.title}\n"
        f"AUTHOR: {bundle.author} | base: {bundle.base_ref} | files: {len(bundle.files)}"
    )
    signals |= preprocess.scan_signals(bundle.title)

    # 2. PR body -------------------------------------------------------------
    if bundle.body.strip():
        signals |= preprocess.scan_signals(bundle.body)
        add(f"\n## PR DESCRIPTION\n{_trim(bundle.body, _TRIM['body'])}")

    # 3. Linked issues (strong symptom signal) -------------------------------
    for li in bundle.linked_issues:
        signals |= preprocess.scan_signals(li.title + "\n" + li.body)
        lab = f" [labels: {', '.join(li.labels)}]" if li.labels else ""
        block = (
            f"\n## LINKED ISSUE #{li.number} ({li.relation}, {li.state}){lab}\n"
            f"TITLE: {li.title}\n{_trim(li.body, _TRIM['issue_body'])}"
        )
        sig_comments = _signal_snippets(li.comments, f"issue#{li.number}", _TRIM["comment"])
        if sig_comments:
            block += "\n" + "\n".join(sig_comments[:3])
        add(block)

    # 4. Commit messages -----------------------------------------------------
    if bundle.commit_messages:
        joined = "\n".join(f"- {m.splitlines()[0] if m else ''}" for m in bundle.commit_messages)
        # Include full bodies too (they often explain the numerical fix).
        full = "\n\n".join(bundle.commit_messages)
        signals |= preprocess.scan_signals(full)
        add(f"\n## COMMIT MESSAGES\n{_trim(joined + '\n\n' + full, _TRIM['commits'])}")

    # 4b. Test-writing commits (the "does this PR ship a test?" evidence) -----
    if bundle.test_commits:
        lines = [
            f"- {tc['sha'][:10]}  {tc['message']}\n    tests: {', '.join(tc['test_files'])}"
            for tc in bundle.test_commits
        ]
        add("\n## TEST-WRITING COMMITS (commits that add lines to a test file)\n"
            + "\n".join(lines))
    elif bundle.test_files:
        # No per-commit attribution, but the PR does touch test files.
        add("\n## TEST FILES TOUCHED (no per-commit attribution)\n"
            + "\n".join(f"- {p}" for p in bundle.test_files))
    else:
        add("\n## TEST-WRITING COMMITS\n(none detected — no commit adds lines to a test file)")

    # 5. Numerically-relevant diff hunks -------------------------------------
    diff_budget = max(2000, max_chars // 3)
    hunks, hunk_sig = _relevant_hunks(bundle.files, diff_budget)
    signals |= hunk_sig
    if hunks:
        add("\n## RELEVANT DIFF HUNKS\n" + "\n\n".join(hunks))
    elif bundle.files:
        listing = "\n".join(
            f"- {f['filename']} (+{f['additions']}/-{f['deletions']}, {f['status']})"
            for f in bundle.files[:40]
        )
        add(f"\n## CHANGED FILES (no numeric-looking hunk detected)\n{listing}")

    # 6. Review + inline comments with numerical mentions --------------------
    review_snips = _signal_snippets(bundle.review_bodies, "review", _TRIM["comment"])
    inline_snips = [
        f"[inline {c['path']}] {_trim(c['body'], _TRIM['comment'])}"
        for c in bundle.review_comments
        if preprocess.scan_signals(c["body"] + " " + c.get("diff_hunk", ""))
    ]
    convo = _signal_snippets(bundle.issue_comments, "comment", _TRIM["comment"])
    all_convo = review_snips + inline_snips + convo
    if all_convo:
        signals |= preprocess.scan_signals(" ".join(all_convo))
        add("\n## DISCUSSION (numerical mentions)\n" + "\n".join(all_convo[:8]))

    # 7. CI log signal regions -----------------------------------------------
    for ci in bundle.ci_logs:
        if not ci.text:
            if ci.note:
                add(f"\n## CI LOG [{ci.name} / {ci.conclusion}]\n{ci.note}")
            continue
        regions = preprocess.extract_regions(ci.text)
        signals |= preprocess.numerical_signals(regions)
        log_budget = max(1500, max_chars // 4)
        excerpt = preprocess.build_excerpt(regions, log_budget)
        if excerpt:
            add(f"\n## CI LOG [{ci.name} / {ci.conclusion}]\n{excerpt}")

    excerpt = "\n".join(sections)
    query = _build_query(bundle, signals)
    return excerpt, sorted(signals), query


def _build_query(bundle: PRBundle, signals: set[str]) -> str:
    """A compact retrieval query: titles + signals + commit subjects."""
    parts = [bundle.title]
    parts += [f"issue: {li.title}" for li in bundle.linked_issues]
    parts += [m.splitlines()[0] for m in bundle.commit_messages[:5] if m]
    if signals:
        parts.append("numerical signals: " + ", ".join(sorted(signals)))
    return "\n".join(p for p in parts if p)[:2000]

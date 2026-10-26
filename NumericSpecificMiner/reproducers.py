"""Keep a reproducer whole.

`numbugminer`'s chunker splits every document at 1200 characters with a 150
character overlap, which is right for prose and wrong for the one passage a
BRTG instance most needs. sympy#26822's trigger is a 7x7 symbolic matrix in the
body of issue gh-26821: 3959 characters, so four chunks, and the matrix lands
across the seams. The mined context recorded the result in its own words —
"matrix not fully shown in the provided passages" — and the agent spent four
iterations unable to construct an input it was never shown.

Rather than re-chunk (the splitter belongs to numbugminer and stays as it is),
this adds chunks: each fenced code block from the PR body, the linked issues and
the comments, emitted WHOLE as its own passage alongside the split prose. A
reproducer is then retrievable in one piece, and the question that asks for
concrete inputs can actually find them.

The blocks come from the same bundle fields the chunker already reads, so this
admits no new source and no withheld material: a view that excludes `issue`
still excludes these.
"""
from __future__ import annotations

import re

# The class `build_documents` emits — `id`/`text`/`metadata`, and
# `views.filter_chunks` reads `c.metadata["source_type"]`. The similarly named
# `numbugminer.docstore.Chunk` has `source`/`ref`/`text` instead, and chunks
# built from it are silently unusable: the filter raises on the missing
# attribute, so they never reach the index.
from numbugminer.chroma_store import Chunk

#: ```python … ``` or ``` … ```, and indented blocks are left alone — a fenced
#: block is how a reporter pastes a reproducer.
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*\n(.*?)```", re.S)

#: a block worth keeping whole: it builds or calls something, rather than being
#: a traceback or a one-line shell snippet
_CODEY = re.compile(r"\b(?:import|from|def|Matrix|array|=\s*\[|\w+\s*\()", re.S)

MIN_CHARS = 60
MAX_CHARS = 8000


def code_blocks(text: str) -> list:
    """The fenced code blocks in one document, longest first."""
    out = []
    for m in _FENCE.finditer(text or ""):
        block = (m.group(1) or "").strip()
        if MIN_CHARS <= len(block) <= MAX_CHARS and _CODEY.search(block):
            out.append(block)
    return sorted(dict.fromkeys(out), key=len, reverse=True)


def _docs(bundle) -> list:
    """`(source_type, label, text)` for every prose document a reporter writes
    a reproducer into. Mirrors what `build_documents` reads."""
    ref = getattr(bundle, "ref", None)
    number = getattr(ref, "number", "") if ref else ""
    docs = [("pr_body", f"PR#{number}",
             f"{getattr(bundle, 'title', '')}\n\n{getattr(bundle, 'body', '') or ''}")]
    for li in (getattr(bundle, "linked_issues", None) or []):
        docs.append(("issue", f"issue#{getattr(li, 'number', '')}",
                     getattr(li, "body", "") or ""))
    for i, c in enumerate(getattr(bundle, "issue_comments", None) or []):
        docs.append(("issue", f"issue comment {i}", c or ""))
    for i, c in enumerate(getattr(bundle, "review_bodies", None) or []):
        docs.append(("discussion", f"review {i}", c or ""))
    return docs


def reproducer_chunks(bundle, limit: int = 12) -> list:
    """Whole-code-block chunks to index ALONGSIDE the split prose.

    Tagged with the source they came from, so `views.filter_chunks` governs
    them exactly as it governs the prose they were cut from.
    """
    base_ref = {"repo": "", "pr_number": "", "commit_sha": "", "heading": "reproducer",
                "file_path": "", "line_start": 0, "line_end": 0}
    ref = getattr(bundle, "ref", None)
    if ref is not None:
        base_ref["repo"] = f"{getattr(ref, 'owner', '')}/{getattr(ref, 'repo', '')}"
        base_ref["pr_number"] = getattr(ref, "number", "")
    base_ref["commit_sha"] = getattr(bundle, "buggy_commit", "") or ""

    out, seen = [], set()
    for source, label, text in _docs(bundle):
        for block in code_blocks(text):
            key = block[:200]
            if key in seen:
                continue
            seen.add(key)
            meta = {**base_ref, "source_type": source, "file_path": label}
            out.append(Chunk(
                id=f"reproducer-{len(out)}-{label}",
                text=f"# REPRODUCER from {label} (kept whole, not split)\n{block}",
                metadata=meta))
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------- #
# issues referenced as `gh-NNNN`
# --------------------------------------------------------------------------- #
#: numbugminer links an issue when the PR says "fixes #26821". SciPy, NumPy and
#: SymPy write "fixes gh-26821", which that pattern does not match:
#:
#:     _extract_linked_issue_numbers("This fixes gh-26821 ...") -> set()
#:     _extract_linked_issue_numbers("This fixes #26821 ...")   -> {26821}
#:
#: so for three of the benchmark's largest repos the referenced issue — its
#: body, its reproducer and its comments — never entered the corpus at all.
#: sympy#26822's 7x7 trigger matrix sits in gh-26821 and was never fetched,
#: which is why the context could only say the matrix was "not fully shown".
#: The splitter was never the problem.
#:
#: numbugminer is not modified; the references it misses are resolved here and
#: fetched through its own client, tagged `issue` exactly as it tags the ones it
#: does find, so `views.filter_chunks` governs them identically.
_GH_REF = re.compile(r"\bgh[-\s]?(\d{3,6})\b", re.I)

#: a reference only counts when the PR claims to FIX it — "see gh-123" is
#: context, not the bug
_GH_FIXES = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|address(?:e[sd])?)\b[:\s]+"
    r"gh[-\s]?(\d{3,6})\b", re.I)


def gh_referenced_issues(bundle, *, strict: bool = True) -> list:
    """Issue numbers the PR references in `gh-NNNN` form and numbugminer missed."""
    text = f"{getattr(bundle, 'title', '')}\n{getattr(bundle, 'body', '') or ''}"
    pattern = _GH_FIXES if strict else _GH_REF
    found = {int(n) for n in pattern.findall(text)}
    ref = getattr(bundle, "ref", None)
    own = {int(getattr(ref, "number", 0) or 0)}
    already = {int(getattr(li, "number", 0) or 0)
               for li in (getattr(bundle, "linked_issues", None) or [])}
    return sorted(found - own - already)


def fetch_gh_issue_chunks(bundle, token=None, limit: int = 3) -> list:
    """Fetch the `gh-NNNN` issues and return them as `issue` chunks.

    Network failures are swallowed: a missing issue leaves the context as it was
    before, which is the behaviour this replaces, and never aborts a paid mine.
    """
    numbers = gh_referenced_issues(bundle)[:limit]
    if not numbers:
        return []
    ref = getattr(bundle, "ref", None)
    owner = getattr(ref, "owner", "") if ref else ""
    repo = getattr(ref, "repo", "") if ref else ""
    if not (owner and repo):
        return []
    try:
        from numbugminer.pr_source import _GitHubClient
        gh = _GitHubClient(token)
    except Exception:                                # noqa: BLE001
        return []
    base = {"repo": f"{owner}/{repo}", "pr_number": getattr(ref, "number", ""),
            "commit_sha": getattr(bundle, "buggy_commit", "") or "",
            "heading": "", "file_path": "", "line_start": 0, "line_end": 0}
    out = []
    for num in numbers:
        try:
            issue = gh.get_json(f"/repos/{owner}/{repo}/issues/{num}",
                                ctx=f"gh-{num}")
        except Exception:                            # noqa: BLE001
            continue
        if not issue or "pull_request" in issue:
            continue
        body = issue.get("body") or ""
        title = issue.get("title") or ""
        label = f"issue#{num}"
        meta = {**base, "source_type": "issue", "file_path": label}
        out.append(Chunk(id=f"gh-issue-{num}", metadata=dict(meta),
                         text=f"ISSUE #{num} (gh-reference)\nTITLE: {title}\n{body}"))
        # and its reproducers, whole
        for i, block in enumerate(code_blocks(body)):
            out.append(Chunk(
                id=f"gh-issue-{num}-repro-{i}",
                metadata={**meta, "heading": "reproducer"},
                text=f"# REPRODUCER from {label} (kept whole, not split)\n{block}"))
        try:
            comments = [c.get("body") or "" for c in
                        gh.paginate(f"/repos/{owner}/{repo}/issues/{num}/comments")]
        except Exception:                            # noqa: BLE001
            comments = []
        for i, c in enumerate(comments[:8]):
            if not c.strip():
                continue
            out.append(Chunk(id=f"gh-issue-{num}-c{i}",
                             metadata={**meta, "heading": f"comment {i}"}, text=c))
            for j, block in enumerate(code_blocks(c)):
                out.append(Chunk(
                    id=f"gh-issue-{num}-c{i}-repro-{j}",
                    metadata={**meta, "heading": f"reproducer c{i}"},
                    text=f"# REPRODUCER from {label} comment (kept whole)\n{block}"))
    return out

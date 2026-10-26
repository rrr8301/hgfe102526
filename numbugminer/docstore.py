from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .pr_source import PRBundle
from .providers import Embedder

# Source-type tags a chunk can carry (used for per-question retrieval focus).
SOURCE_TYPES = ("pr_meta", "pr_body", "issue", "discussion", "diff", "test_diff",
                "commit", "readme", "ci_yaml", "lockfile")

_CHUNK_CHARS = 1200
_OVERLAP = 150


@dataclass(frozen=True)
class Chunk:
    source: str   # one of SOURCE_TYPES
    ref: str      # human-readable origin (file path, issue #, ...)
    text: str


def _split(text: str, size: int = _CHUNK_CHARS, overlap: int = _OVERLAP) -> list[str]:
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return out


def _hunks(patch: str) -> list[str]:
    """Split a unified-diff file patch into @@ hunks."""
    if not patch:
        return []
    blocks, cur = [], []
    for line in patch.splitlines():
        if line.startswith("@@") and cur:
            blocks.append("\n".join(cur)); cur = [line]
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _is_test_path(path: str) -> bool:
    from .pr_source import is_test_file
    return is_test_file(path)


def build_documents(bundle: PRBundle, repo_files: list | None = None) -> list[Chunk]:
    """Assemble the full chunk list from PR evidence + cloned repo files."""
    chunks: list[Chunk] = []

    def add(source: str, ref: str, text: str):
        for piece in _split(text):
            chunks.append(Chunk(source, ref, piece))

    r = bundle.ref
    add("pr_meta", f"PR #{r.number}",
        f"PULL REQUEST {r.owner}/{r.repo}#{r.number} ({'merged' if bundle.merged else bundle.state})\n"
        f"TITLE: {bundle.title}\nAUTHOR: {bundle.author} | base: {bundle.base_ref} | "
        f"labels: {', '.join(bundle.labels)}")
    add("pr_body", f"PR #{r.number} body", bundle.title + "\n\n" + bundle.body)

    for li in bundle.linked_issues:
        body = f"ISSUE #{li.number} ({li.relation}, {li.state})\nTITLE: {li.title}\n{li.body}"
        add("issue", f"issue #{li.number}", body)
        for c in li.comments[:6]:
            add("issue", f"issue #{li.number} comment", c)

    for c in bundle.issue_comments[:12]:
        add("discussion", "pr comment", c)
    for rv in bundle.review_bodies[:12]:
        add("discussion", "review", rv)
    for rc in bundle.review_comments[:20]:
        add("discussion", f"inline {rc.get('path','')}",
            rc.get("body", "") + "\n" + rc.get("diff_hunk", ""))

    if bundle.commit_messages:
        add("commit", "commit messages", "\n\n".join(bundle.commit_messages))

    for f in bundle.files:
        fname = f.get("filename", "")
        src = "test_diff" if _is_test_path(fname) else "diff"
        for h in _hunks(f.get("patch", "")):
            chunks.append(Chunk(src, fname, f"--- {fname} ---\n{h}"))

    for rf in (repo_files or []):
        add(rf.role, rf.path, f"# FILE: {rf.path}\n{rf.text}")

    return [c for c in chunks if c.text.strip()]


@dataclass
class DocIndex:
    embedder: Embedder
    chunks: list[Chunk]
    vectors: np.ndarray  # (n, d), L2-normalized

    @classmethod
    def build(cls, embedder: Embedder, chunks: list[Chunk]) -> "DocIndex":
        texts = [c.text for c in chunks]
        embedder.fit(texts)
        vectors = embedder.encode(texts) if texts else np.zeros((0, 1), dtype=np.float32)
        return cls(embedder, chunks, vectors)

    def search(self, query: str, k: int = 6,
               sources: tuple[str, ...] | None = None) -> list[tuple[Chunk, float]]:
        """Top-k chunks by cosine similarity. If `sources` is given, restrict to
        those source types when at least k of them exist, else fall back to all."""
        if not self.chunks:
            return []
        idx = list(range(len(self.chunks)))
        if sources:
            focused = [i for i in idx if self.chunks[i].source in sources]
            if len(focused) >= k:
                idx = focused
        q = self.embedder.encode([query])[0]
        sims = self.vectors[idx] @ q
        order = np.argsort(-sims)[:k]
        return [(self.chunks[idx[j]], float(sims[j])) for j in order]

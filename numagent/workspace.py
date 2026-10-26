from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from .shell import run


def _is_url(src: str) -> bool:
    return src.startswith(("http://", "https://", "git@", "ssh://"))


def _clone_at_commit(src: str, dest: Path, ref: str) -> str:
    """Fetch one exact commit and check it out (shallow). GitHub allows fetching
    a SHA directly, so we avoid cloning the whole history."""
    dest.mkdir(parents=True, exist_ok=True)
    steps = [
        ["git", "init", "-q", str(dest)],
        ["git", "-C", str(dest), "remote", "add", "origin", src],
        ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", ref],
        ["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"],
    ]
    for cmd in steps:
        res = run(cmd, timeout_s=600)
        if not res.ok:
            raise RuntimeError(f"checkout of {ref[:12]} failed at `{' '.join(cmd[:3])}…`:\n{res.stderr}")
    return str(dest)


def apply_patch(repo_path: str, patch: str) -> bool:
    """Apply a unified-diff `patch` to the checked-out repo (git apply, with a
    --3way fallback for context drift). The patch file is written outside the
    repo so it never dirties the tree. Returns whether it applied."""
    repo = Path(repo_path).resolve()
    if not patch.strip():
        return False
    pf = repo.parent / f"{repo.name}.preprocess.patch"
    pf.write_text(patch, encoding="utf-8")
    res = run(["git", "-C", str(repo), "apply", "--verbose", str(pf)], timeout_s=120)
    if not res.ok:
        res = run(["git", "-C", str(repo), "apply", "--3way", str(pf)], timeout_s=120)
    return res.ok


def prepare(src: str, workdir: Optional[str] = None, ref: Optional[str] = None) -> str:
    """Return a local path to a working copy of `src`.

    - git URL, no ref  -> shallow clone of the default branch
    - git URL + ref     -> fetch that exact commit and check it out (used to land
                          on a bug's "buggy" commit supplied by the miner)
    - local dir         -> copied into workdir (source tree stays untouched)
    """
    base = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="numagent_"))
    base.mkdir(parents=True, exist_ok=True)

    if _is_url(src):
        dest = base / "repo"
        if dest.exists():
            shutil.rmtree(dest)
        if ref:
            return _clone_at_commit(src, dest, ref)
        res = run(["git", "clone", "--depth", "1", src, str(dest)], timeout_s=600)
        if not res.ok:
            raise RuntimeError(f"clone failed:\n{res.stderr}")
        return str(dest)

    srcp = Path(src).expanduser().resolve()
    if not srcp.exists():
        raise FileNotFoundError(src)
    dest = base / srcp.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        srcp,
        dest,
        ignore=shutil.ignore_patterns(".git", "build", "_build", "__pycache__", "*.egg-info"),
    )
    return str(dest)

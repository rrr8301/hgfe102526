from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .pr_source import added_test_constructs, is_test_file

# Files that describe how to build / test / pin the project, by role. Names are
# matched at the repo root (and a couple of common subdirs); CI YAML is gathered
# recursively from CI directories.
_README = ("README.md", "README.rst", "README.txt", "README")
# dependency / package manifests (pins + language)
_MANIFESTS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "requirements-dev.txt", "requirements_dev.txt", "dev-requirements.txt",
    "environment.yml", "environment.yaml", "Pipfile", "Pipfile.lock",
    "poetry.lock", "uv.lock", "constraints.txt", "tox.ini",
    "package.json", "pom.xml", "build.gradle", "build.gradle.kts",
    "go.mod", "Cargo.toml", "Gemfile", "composer.json",
)
# build / containerization scripts
_BUILD = (
    "Dockerfile", "Makefile", "makefile", "GNUmakefile", "CMakeLists.txt",
    "noxfile.py", ".python-version", "meson.build", "conftest.py",
    "docker-compose.yml", "docker-compose.yaml",
)
# dependency/pin manifests keep the "lockfile" role (probe/docstore rely on it);
# build/containerization scripts get the new "build_config" role.
_ROLE = {**{n: "lockfile" for n in _MANIFESTS}, **{n: "build_config" for n in _BUILD}}
# extra dirs to scan one level deep for Dockerfiles / build files
_BUILD_SUBDIRS = ("docker", ".devcontainer", "ci", "build", "scripts")
_CI_DIRS = (".github/workflows", ".circleci", "ci", ".azure-pipelines", ".gitlab")
_CI_GLOBS = ("*.yml", "*.yaml")
_MAX_FILE_CHARS = 40_000


@dataclass
class RepoFile:
    role: str      # "readme" | "ci_yaml" | "lockfile" | "build_config" | "template"
    path: str      # repo-relative
    text: str


@dataclass
class RepoState:
    repo: str
    url: str
    commit: str
    worktree: Path
    files: list[RepoFile] = field(default_factory=list)
    note: str = ""


def _run(args: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, check=False)


def _clear_stale_locks(dest: Path) -> list:
    """Remove lock files a KILLED git left behind in a reused clone.

    The clone is reused between runs, so a run stopped mid-`git reset` leaves
    `.git/index.lock` in place, and every later run of that instance then dies
    on "Unable to create '…/index.lock': File exists" before checking anything
    out. The process that held the lock is gone with the run, so the leftovers
    are cleared rather than inherited.
    """
    removed = []
    git_dir = Path(dest) / ".git"
    for name in ("index.lock", "HEAD.lock", "config.lock", "shallow.lock",
                 "packed-refs.lock"):
        lock = git_dir / name
        try:
            if lock.exists():
                lock.unlink()
                removed.append(name)
        except OSError:
            pass
    return removed


def clone_and_reset(repo: str, commit: str, dest: Path, *,
                    token: str | None = None, timeout: int = 1800) -> RepoState:
    """Full-clone ``owner/repo`` into ``dest`` and ``git reset --hard`` to `commit`.

    Reuses an existing clone in `dest` if present (fetch + reset). A token, when
    given, authenticates the clone URL for private repos / higher rate limits.
    """
    owner_repo = repo.strip().removesuffix(".git")
    if owner_repo.startswith("http"):
        owner_repo = owner_repo.split("github.com/", 1)[-1]
    url = f"https://github.com/{owner_repo}.git"
    auth_url = (f"https://x-access-token:{token}@github.com/{owner_repo}.git"
                if token else url)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").exists():
        r = _run(["git", "clone", auth_url, str(dest)], timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {r.stderr.strip()[:300]}")
    else:
        _run(["git", "fetch", "--all", "--quiet"], cwd=dest, timeout=timeout)

    if commit:
        _clear_stale_locks(dest)
        r = _run(["git", "reset", "--hard", commit], cwd=dest)
        if r.returncode != 0:
            # commit may not be present locally — fetch it explicitly, then retry
            _run(["git", "fetch", "origin", commit], cwd=dest, timeout=timeout)
            r = _run(["git", "reset", "--hard", commit], cwd=dest)
            if r.returncode != 0:
                raise RuntimeError(f"git reset to {commit[:12]} failed: {r.stderr.strip()[:200]}")
    _run(["git", "clean", "-fdq"], cwd=dest)
    # Vendored submodules are part of the source: scipy's build stops dead with
    # "Missing the `array_api_compat` submodule" without them. Guarded on
    # .gitmodules so a repo that has none pays nothing, and shallow because only
    # the checked-out tree is ever used.
    if (dest / ".gitmodules").exists():
        _run(["git", "submodule", "update", "--init", "--recursive", "--depth", "1"],
             cwd=dest, timeout=timeout)

    state = RepoState(repo=owner_repo, url=url, commit=commit, worktree=dest)
    state.files = _gather_files(dest)
    return state


# --- test-only patch: expose the bug on the buggy checkout ------------------

_DIFF_HDR = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>\S+)\s*$", re.M)


def _split_diff(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into (new_path, per-file block)."""
    if not diff:
        return []
    parts = re.split(r"(?m)^(?=diff --git )", diff)
    out: list[tuple[str, str]] = []
    for block in parts:
        m = _DIFF_HDR.match(block)
        if m:
            out.append((m.group("b"), block))
    return out


_HUNK_START = re.compile(r"^@@ ", re.M)


def _split_hunks(block: str) -> tuple[str, list[str]]:
    """One file's diff block as (header, [hunk, ...])."""
    marks = [m.start() for m in _HUNK_START.finditer(block)]
    if not marks:
        return block, []
    header = block[:marks[0]]
    bounds = marks + [len(block)]
    return header, [block[bounds[i]:bounds[i + 1]] for i in range(len(marks))]


def _partition_block(path: str, block: str) -> tuple[str, str]:
    """Split ONE file's diff into (test part, source part).

    A path-only rule cannot separate them in a language that keeps its unit tests
    inside the production file: Rust's `#[cfg(test)] mod tests` sits in the same
    .rs file as the fix, so the whole file lands in the source patch and the test
    patch comes out EMPTY — leaving the buggy revision with no reproducing test
    and no fail→pass oracle. Hunks whose added lines introduce test constructs go
    to the test patch; the rest stay with the fix.
    """
    if is_test_file(path):
        return block, ""
    header, hunks = _split_hunks(block)
    if not hunks:
        return "", block
    test_hunks = [h for h in hunks if added_test_constructs(h)]
    if not test_hunks:
        return "", block
    source_hunks = [h for h in hunks if h not in test_hunks]
    test_part = header + "".join(test_hunks)
    source_part = (header + "".join(source_hunks)) if source_hunks else ""
    return test_part, source_part


def extract_test_patch(diff: str) -> tuple[str, list[str]]:
    """Keep ONLY the test changes of `diff`. Returns (patch_text, paths).

    Isolating the test changes lets us apply the PR's new/modified tests onto the
    buggy commit without also applying the fix — so the test now runs against
    buggy code and fails, exposing the bug (the fail→pass oracle)."""
    parts, paths = [], []
    for path, block in _split_diff(diff):
        test_part, _ = _partition_block(path, block)
        if test_part.strip():
            parts.append(test_part)
            paths.append(path)
    return "".join(parts), paths


def extract_source_patch(diff: str) -> tuple[str, list[str]]:
    """Keep ONLY the fix — the changes that are not test code.
    Returns (patch_text, paths). Complement of :func:`extract_test_patch`."""
    parts, paths = [], []
    for path, block in _split_diff(diff):
        _, source_part = _partition_block(path, block)
        if source_part.strip():
            parts.append(source_part)
            paths.append(path)
    return "".join(parts), paths


def split_patch(diff: str) -> dict:
    """Split a PR diff into its source (fix) and test parts.

    Returns ``{source_patch, source_files, test_patch, test_files}`` — the gold
    fix vs the reproducing test, so an agent harness can withhold the fix and
    apply only the test to expose the bug on the buggy commit."""
    src, src_files = extract_source_patch(diff)
    test, test_files = extract_test_patch(diff)
    return {"source_patch": src, "source_files": src_files,
            "test_patch": test, "test_files": test_files}


def _apply(worktree: Path, patch_file: Path, *, three_way: bool) -> subprocess.CompletedProcess:
    args = ["git", "apply", "--verbose"]
    if three_way:
        args.append("--3way")
    return _run(args + [str(patch_file)], cwd=worktree)


def apply_test_patch(worktree: Path, diff: str) -> dict:
    """Apply only the test-file hunks of `diff` to the buggy `worktree`.

    Tries a plain ``git apply``; on context drift falls back to ``--3way``. The
    patch file is written OUTSIDE the worktree so a later ``git clean`` can't nuke
    it. Returns {applied, files, error, patch_path}.
    """
    worktree = Path(worktree).resolve()  # git apply runs with cwd=worktree, needs abs patch path
    patch, files = extract_test_patch(diff)
    if not patch.strip():
        return {"applied": False, "files": [], "error": "no test-file changes in diff",
                "patch_path": ""}

    pf = worktree.parent / f"{worktree.name}.test.patch"
    pf.write_text(patch, encoding="utf-8")

    check = _run(["git", "apply", "--check", str(pf)], cwd=worktree)
    if check.returncode == 0:
        res = _apply(worktree, pf, three_way=False)
    else:
        res = _apply(worktree, pf, three_way=True)  # context drifted — 3-way merge
    ok = res.returncode == 0
    return {"applied": ok, "files": files, "patch_path": str(pf),
            "error": "" if ok else (res.stderr or check.stderr).strip()[:400]}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_MAX_FILE_CHARS]
    except OSError:
        return ""


#: The repository's own PR/issue boilerplate. Every PR body starts as a copy of
#: this, so anything it contains — contribution policies, release-note guides,
#: git tutorials — is repository boilerplate rather than evidence about a bug.
#: Collected so those links can be excluded, not so they can be indexed.
_TEMPLATE_GLOBS = (
    ".github/PULL_REQUEST_TEMPLATE*", ".github/PULL_REQUEST_TEMPLATE/*",
    ".github/ISSUE_TEMPLATE*", ".github/ISSUE_TEMPLATE/*",
    "PULL_REQUEST_TEMPLATE*", "ISSUE_TEMPLATE*",
    "docs/PULL_REQUEST_TEMPLATE*", ".gitlab/merge_request_templates/*",
    ".gitlab/issue_templates/*",
)


def _gather_files(root: Path) -> list[RepoFile]:
    """Collect README, CI YAML, dependency manifests, build/config scripts, and
    the PR/issue templates.

    Roles: readme | ci_yaml | manifest | build_config | template. Names are
    matched at the repo root and a few common subdirs; CI YAML is gathered
    recursively from CI directories. Kept bounded (no whole-tree walk).
    """
    out: list[RepoFile] = []
    seen: set[str] = set()

    def add(role: str, path: Path):
        rel = str(path.relative_to(root))
        if rel not in seen and path.is_file():
            seen.add(rel)
            out.append(RepoFile(role, rel, _read(path)))

    for name in _README:
        p = root / name
        if p.is_file():
            add("readme", p)
            break

    for rel in _CI_DIRS:
        d = root / rel
        if d.is_dir():
            for glob in _CI_GLOBS:
                for p in sorted(d.rglob(glob)):
                    add("ci_yaml", p)

    # manifests + build/config: root level, plus a shallow scan of common subdirs
    for name, role in _ROLE.items():
        p = root / name
        if p.is_file():
            add(role, p)
    for sub in _BUILD_SUBDIRS:
        d = root / sub
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and p.name in _ROLE:
                    add(_ROLE[p.name], p)

    for glob in _TEMPLATE_GLOBS:
        for p in sorted(root.glob(glob)):
            if p.is_file():
                add("template", p)

    return out

from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

from . import downloader

API_ROOT = "https://api.github.com"

# https://github.com/OWNER/REPO/pull/NUM   (tolerates .diff/.patch suffixes)
_PR_URL = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/\s]+)/pull/(?P<num>\d+)"
)
_SHORTHAND = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/#]+)#(?P<num>\d+)$")

# A path is a "test file" if it lives under a tests/ dir, is a pytest-style test
# module (test_foo.py / foo_test.py), or a JS spec/test file. Broad enough for
# the numerical repos we target while not matching production modules that
# merely have "test" inside a longer word (e.g. latest_feature.py).
# LEVEL 1 — language-independent path conventions.
_TEST_PATH = re.compile(
    r"""
      (?:^|/)tests?/                 # a  tests/  or  test/  directory anywhere
    | (?:^|/)[^/]*[-_]tests?/        # a dedicated test CRATE/package: burn's
                                     # onnx-tests/, pytorch-tests/, integration_test/.
                                     # Everything in it is test infrastructure —
                                     # its build.rs generates the test modules, so
                                     # leaving that on the source side gives the
                                     # buggy revision tests it cannot even build.
    | (?:^|/)__tests__/              # the JS convention
    | (?:^|/)test_[^/]+$             # test_foo.py
    | (?:^|/)[^/]*_tests?\.[^/.]+$   # foo_test.py / foo_tests.go
    | (?:^|/)[^/]*_unittests?\.[^/.]+$   # foo_unittest.cc
    | (?:^|/)[^/]*Tests?\.(?:java|kt|scala|cs)$      # FooTest.java, FooTests.kt
    | (?:^|/)[^/]*TestCase\.(?:java|kt|scala|cs)$    # FooTestCase.java
    | \.spec\.[^/.]+$                # foo.spec.js / .ts / .rb
    | \.test\.[^/.]+$                # foo.test.js / .ts
    """,
    re.IGNORECASE | re.VERBOSE,
)

# LEVEL 2 — framework constructs, matched against ADDED diff lines. This is what
# finds a test that path conventions cannot: Rust keeps its unit tests inside the
# production source file, so `src/base/statistics.rs` gaining `#[test]` is a
# test-writing change that no filename rule will ever see.
_TEST_CONSTRUCTS = {
    "python": re.compile(r"^\s*(?:def\s+test_\w+|class\s+Test\w+|@pytest\.mark)"),
    "java": re.compile(r"^\s*(?:@Test\b|@ParameterizedTest\b|@RepeatedTest\b)"),
    "go": re.compile(r"^\s*func\s+(?:Test|Benchmark|Fuzz|Example)\w*\s*\("),
    "rust": re.compile(r"^\s*#\[(?:test|cfg\(test\)|should_panic)"),
    "cpp": re.compile(r"^\s*(?:TEST(?:_F|_P)?\s*\(|TEST_CASE\s*\(|BOOST_AUTO_TEST_CASE\s*\("
                      r"|SCENARIO\s*\(|add_test\s*\()", re.I),
    "js": re.compile(r"^\s*(?:it|test|describe)\s*(?:\.\w+)?\s*\("),
}

#: how a test case is NAMED, per construct family — recorded so reproduction can
#: point at the case rather than the whole file
_TEST_CASE_NAME = (
    re.compile(r"^\s*def\s+(test_\w+)"),                       # python
    re.compile(r"^\s*func\s+(Test\w+)"),                       # go
    re.compile(r"^\s*(?:public\s+)?(?:void|fn)\s+(\w*[Tt]est\w*)"),   # java / rust
    re.compile(r"^\s*fn\s+(\w+)"),                             # rust (after #[test])
    re.compile(r"""^\s*(?:it|test|describe)\s*\(\s*['"`]([^'"`]{1,120})"""),  # js
    re.compile(r"^\s*TEST(?:_F|_P)?\s*\(\s*(\w+\s*,\s*\w+)"),   # gtest
    re.compile(r"""^\s*TEST_CASE\s*\(\s*['"]([^'"]{1,120})"""),   # catch2
)


def _added_lines(patch: str) -> list:
    """The lines a diff ADDS (not context, not removals, not the +++ header)."""
    return [ln[1:] for ln in (patch or "").splitlines()
            if ln.startswith("+") and not ln.startswith("+++")]


def added_test_constructs(patch: str) -> list:
    """Which framework's test constructs appear among a diff's added lines."""
    added = _added_lines(patch)
    return sorted({name for name, pattern in _TEST_CONSTRUCTS.items()
                   if any(pattern.search(line) for line in added)})


def test_cases_in_patch(patch: str) -> list:
    """Names of the test cases a diff introduces, best effort and de-duplicated."""
    names, seen = [], set()
    for line in _added_lines(patch):
        for pattern in _TEST_CASE_NAME:
            m = pattern.search(line)
            if m:
                name = m.group(1).strip()
                if name and name.lower() not in seen and not name.lower().startswith("test_case"):
                    seen.add(name.lower())
                    names.append(name)
                break
    return names[:40]


def writes_tests(path: str, patch: str = "") -> bool:
    """Does this file change write test cases?

    True when a recognised test FILE gains lines, or when any file — production
    source included — gains a framework test construct. The second clause is not
    a nicety: without it every Rust instance in the benchmark looks test-free.
    """
    added = _added_lines(patch)
    if is_test_file(path) and (added or not patch):
        return True
    return bool(added) and bool(added_test_constructs(patch))


def is_test_file(path: str) -> bool:
    """True if `path` looks like a test file (not a production module)."""
    return bool(path) and bool(_TEST_PATH.search(path))

# "Fixes #12", "closes  #34", "resolved: #5" and the full-URL variant.
_CLOSING_KW = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#(?P<num>\d+)", re.I
)
_CLOSING_URL = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+"
    r"https?://github\.com/[^/]+/[^/]+/issues/(?P<num>\d+)",
    re.I,
)


@dataclass(frozen=True)
class PRRef:
    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}_{self.repo}_pr{self.number}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"


@dataclass
class LinkedIssue:
    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    relation: str  # "closes" | "cross-referenced"
    comments: list[str] = field(default_factory=list)


@dataclass
class CILog:
    run_id: int
    name: str
    conclusion: str
    text: str = ""
    note: str = ""


@dataclass
class PRBundle:
    ref: PRRef
    title: str
    body: str
    state: str
    merged: bool
    author: str
    head_sha: str
    base_ref: str
    labels: list[str]
    diff: str
    files: list[dict]
    commit_messages: list[str]
    issue_comments: list[str]
    review_bodies: list[str]
    review_comments: list[dict]
    linked_issues: list[LinkedIssue]
    ci_logs: list[CILog]
    # Commits on the branch that write test cases (add lines to a test file),
    # and the union of test files they touch. Fuels the RAG "does this PR ship a
    # test for the numerical bug?" question.
    test_commits: list[dict] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    # The before/after commit pair for reproducing the bug in a benchmark.
    base_sha: str = ""            # base-branch tip the PR targeted
    merge_commit_sha: str = ""    # merge/squash commit, when merged
    fixed_commit: str = ""        # commit whose tree contains the fix
    buggy_commit: str = ""        # commit whose tree still has the bug
    commit_pair_note: str = ""    # how the pair was derived
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "url": self.ref.html_url,
            "owner": self.ref.owner,
            "repo": self.ref.repo,
            "number": self.ref.number,
            "title": self.title,
            "state": self.state,
            "merged": self.merged,
            "author": self.author,
            "head_sha": self.head_sha,
            "base_ref": self.base_ref,
            "base_sha": self.base_sha,
            "merge_commit_sha": self.merge_commit_sha,
            "fixed_commit": self.fixed_commit,
            "buggy_commit": self.buggy_commit,
            "commit_pair_note": self.commit_pair_note,
            "labels": self.labels,
            "n_files": len(self.files),
            "n_commits": len(self.commit_messages),
            "n_issue_comments": len(self.issue_comments),
            "n_review_comments": len(self.review_comments),
            "linked_issues": [
                {"number": li.number, "title": li.title, "relation": li.relation}
                for li in self.linked_issues
            ],
            "ci_logs": [
                {"run_id": c.run_id, "name": c.name, "conclusion": c.conclusion,
                 "chars": len(c.text), "note": c.note}
                for c in self.ci_logs
            ],
            "test_commits": self.test_commits,
            "test_files": self.test_files,
            "warnings": self.warnings,
        }


def parse_pr_ref(ref: str, repo: str | None = None) -> PRRef:
    """Turn a user-supplied PR reference into a PRRef."""
    m = _PR_URL.search(ref)
    if m:
        repo_name = m["repo"]
        if repo_name.endswith(".git"):
            repo_name = repo_name[: -len(".git")]
        return PRRef(m["owner"], repo_name, int(m["num"]))

    m = _SHORTHAND.match(ref.strip())
    if m:
        return PRRef(m["owner"], m["repo"], int(m["num"]))

    if ref.strip().isdigit():
        if not repo or "/" not in repo:
            raise ValueError(
                "A bare PR number needs --repo OWNER/REPO (e.g. --repo numpy/numpy)."
            )
        owner, name = repo.split("/", 1)
        return PRRef(owner, name, int(ref.strip()))

    raise ValueError(f"Could not parse PR reference: {ref!r}")


def is_pr_ref(ref: str) -> bool:
    return bool(_PR_URL.search(ref) or _SHORTHAND.match(ref.strip()))


class _GitHubClient:
    """A thin authenticated GitHub REST client with pagination + fail-soft GETs."""

    def __init__(self, token: str | None, timeout: int = 60):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "numbugminer",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _raise_for(self, resp: requests.Response, ctx: str) -> None:
        if resp.status_code == 401:
            raise RuntimeError(f"{ctx}: 401 Unauthorized — check GITHUB_TOKEN.")
        if resp.status_code == 403 and resp.headers.get("X-RateLimit-Remaining") == "0":
            raise RuntimeError(
                f"{ctx}: GitHub rate limit exhausted. Set GITHUB_TOKEN in .env "
                "to raise it (60/hr unauthenticated -> 5000/hr authenticated)."
            )
        resp.raise_for_status()

    def get_json(self, path: str, *, accept: str | None = None, ctx: str = ""):
        headers = {"Accept": accept} if accept else {}
        resp = self.session.get(f"{API_ROOT}{path}", headers=headers, timeout=self.timeout)
        self._raise_for(resp, ctx or path)
        return resp.json()

    def get_text(self, path: str, *, accept: str, ctx: str = "") -> str:
        resp = self.session.get(
            f"{API_ROOT}{path}", headers={"Accept": accept}, timeout=self.timeout
        )
        self._raise_for(resp, ctx or path)
        return resp.text

    def paginate_iter(self, path: str, *, accept: str | None = None,
                      cap: int | None = 500):
        """Yield items page by page, following RFC-5988 Link rel=next.

        Lazy on purpose: a caller that stops at a date boundary should pay for
        the pages it actually read. Fetching `cap` items first and filtering
        afterwards makes a one-month window cost the same as a five-year one.
        `cap=None` reads until the listing is exhausted.
        """
        sep = "&" if "?" in path else "?"
        url = f"{API_ROOT}{path}{sep}per_page=100"
        headers = {"Accept": accept} if accept else {}
        seen = 0
        while url and (cap is None or seen < cap):
            resp = self.session.get(url, headers=headers, timeout=self.timeout)
            self._raise_for(resp, path)
            batch = resp.json()
            if not isinstance(batch, list):
                return
            for item in batch:
                if cap is not None and seen >= cap:
                    return
                seen += 1
                yield item
            url = resp.links.get("next", {}).get("url")

    def paginate(self, path: str, *, accept: str | None = None,
                 cap: int | None = 500) -> list:
        """Follow RFC-5988 Link rel=next, up to `cap` items."""
        return list(self.paginate_iter(path, accept=accept, cap=cap))


def commits_writing_tests(
    gh: "_GitHubClient", owner: str, repo: str, number: int,
    *, cap: int = 50, commits: list | None = None,
) -> list[dict]:
    """Commits on the PR branch that write test cases (add lines to a test file).

    Each is ``{sha, message, test_files}``. Per-commit files come from the commit
    endpoint; pass an already-fetched `commits` list to avoid re-paginating. A
    commit that can't be fetched is skipped, not fatal.
    """
    if commits is None:
        commits = gh.paginate(f"/repos/{owner}/{repo}/pulls/{number}/commits", cap=cap)
    out: list[dict] = []
    for c in commits:
        sha = c.get("sha")
        if not sha:
            continue
        try:
            detail = gh.get_json(f"/repos/{owner}/{repo}/commits/{sha}",
                                 ctx=f"commit {sha[:7]}")
        except Exception:  # noqa: BLE001 — one bad commit shouldn't drop the PR
            continue
        tfs, cases, added = [], [], 0
        for f in (detail.get("files") or []):
            name, patch = f.get("filename", ""), f.get("patch", "")
            if f.get("additions", 0) <= 0 or not writes_tests(name, patch):
                continue
            tfs.append(name)
            cases.extend(c for c in test_cases_in_patch(patch) if c not in cases)
            added += int(f.get("additions") or 0)
        if tfs:
            msg_lines = ((c.get("commit") or {}).get("message") or "").splitlines()
            out.append({
                "sha": sha,
                "message": (msg_lines[0][:120] if msg_lines else ""),
                "test_files": tfs,
                # what the commit actually introduced — reproduction points at a
                # case, not a file, and the line count separates a real test from
                # a one-line tweak to an existing one
                "test_cases": cases[:40],
                "added_test_lines": added,
            })
    return out


def _extract_linked_issue_numbers(text: str) -> set[int]:
    nums: set[int] = set()
    for pat in (_CLOSING_KW, _CLOSING_URL):
        nums.update(int(m["num"]) for m in pat.finditer(text or ""))
    return nums


def fetch_pr_bundle(
    ref: PRRef,
    token: str | None,
    *,
    include_logs: bool = True,
    include_commit_tests: bool = True,
    max_linked_issues: int = 6,
    max_ci_runs: int = 4,
    max_commit_tests: int = 50,
    max_log_chars: int = 300_000,
) -> PRBundle:
    """Fetch the full evidence bundle for a PR. Core fields are required; the
    conversation / linked-issue / CI pieces fail soft into `warnings`."""
    gh = _GitHubClient(token)
    o, r, n = ref.owner, ref.repo, ref.number
    warnings: list[str] = []

    def soft(label: str, fn, default):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — degrade, don't abort
            warnings.append(f"{label}: {type(e).__name__}: {e}")
            return default

    # --- Core: PR metadata + diff (fatal on failure) --------------------------
    pr = gh.get_json(f"/repos/{o}/{r}/pulls/{n}", ctx="PR metadata")
    diff = soft("diff", lambda: gh.get_text(
        f"/repos/{o}/{r}/pulls/{n}", accept="application/vnd.github.diff", ctx="diff"
    ), "")

    files = soft("files", lambda: [
        {"filename": f["filename"], "status": f["status"],
         "additions": f.get("additions", 0), "deletions": f.get("deletions", 0),
         "patch": f.get("patch", "")}
        for f in gh.paginate(f"/repos/{o}/{r}/pulls/{n}/files")
    ], [])

    commits_raw = soft("commits", lambda: gh.paginate(f"/repos/{o}/{r}/pulls/{n}/commits"), [])
    commit_messages = [c["commit"]["message"] for c in commits_raw
                       if (c.get("commit") or {}).get("message")]

    # Which commits actually write a test case (add lines to a test file)? This
    # is the deterministic evidence the RAG classifier is asked to judge. Also
    # note test files the PR touches overall (free from the already-fetched
    # `files`) as a fallback when per-commit detail is off/unavailable.
    pr_test_files = sorted({f["filename"] for f in files
                            if is_test_file(f.get("filename", "")) and f.get("additions", 0) > 0})
    test_commits: list[dict] = []
    if include_commit_tests and commits_raw:
        test_commits = soft("commit test detection", lambda: commits_writing_tests(
            gh, o, r, n, cap=max_commit_tests, commits=commits_raw), [])
    test_files = sorted({f for tc in test_commits for f in tc["test_files"]}) or pr_test_files

    issue_comments = soft("issue comments", lambda: [
        c["body"] for c in gh.paginate(f"/repos/{o}/{r}/issues/{n}/comments") if c.get("body")
    ], [])

    reviews = soft("reviews", lambda: gh.paginate(f"/repos/{o}/{r}/pulls/{n}/reviews"), [])
    review_bodies = [rv["body"] for rv in reviews if rv.get("body")]

    review_comments = soft("review comments", lambda: [
        {"path": c.get("path", ""), "body": c.get("body", ""),
         "diff_hunk": c.get("diff_hunk", "")}
        for c in gh.paginate(f"/repos/{o}/{r}/pulls/{n}/comments") if c.get("body")
    ], [])

    # --- Linked issues: closing keywords (strong) + timeline cross-refs -------
    linked_numbers: dict[int, str] = {}
    for num in _extract_linked_issue_numbers(f"{pr.get('title','')}\n{pr.get('body','')}"):
        linked_numbers[num] = "closes"

    timeline = soft("timeline", lambda: gh.paginate(
        f"/repos/{o}/{r}/issues/{n}/timeline",
        accept="application/vnd.github+json",
    ), [])
    for ev in timeline:
        if ev.get("event") == "cross-referenced":
            src = (ev.get("source") or {}).get("issue") or {}
            # Only actual issues (issues have no pull_request key) in this repo.
            if src.get("number") and "pull_request" not in src:
                linked_numbers.setdefault(int(src["number"]), "cross-referenced")

    linked_issues: list[LinkedIssue] = []
    for num, relation in list(linked_numbers.items())[:max_linked_issues]:
        issue = soft(f"issue #{num}", lambda num=num: gh.get_json(
            f"/repos/{o}/{r}/issues/{num}", ctx=f"issue #{num}"), None)
        if not issue or "pull_request" in issue:  # skip PRs masquerading as issues
            continue
        comments = soft(f"issue #{num} comments", lambda num=num: [
            c["body"] for c in gh.paginate(f"/repos/{o}/{r}/issues/{num}/comments")
            if c.get("body")
        ], [])
        linked_issues.append(LinkedIssue(
            number=num,
            title=issue.get("title", ""),
            body=issue.get("body") or "",
            labels=[l["name"] for l in issue.get("labels", [])],
            state=issue.get("state", ""),
            relation=relation,
            comments=comments,
        ))

    # --- Buggy / fixed commit pair (for reproduction) -------------------------
    head_sha = (pr.get("head") or {}).get("sha", "")
    base_sha = (pr.get("base") or {}).get("sha", "") or ""
    merge_commit_sha = pr.get("merge_commit_sha") or ""
    fixed_commit = buggy_commit = ""
    pair_note = ""
    if pr.get("merged") and merge_commit_sha:
        # The merge/squash commit carries the fix; its first parent is the base
        # state immediately before it — i.e. the code still containing the bug.
        fixed_commit = merge_commit_sha
        commit = soft("merge commit", lambda: gh.get_json(
            f"/repos/{o}/{r}/commits/{merge_commit_sha}", ctx="merge commit"), None)
        parents = (commit or {}).get("parents", []) if commit else []
        if parents:
            buggy_commit = parents[0]["sha"]
            pair_note = ("fixed = merge commit; buggy = its first parent "
                         f"({len(parents)} parent(s))")
        else:
            buggy_commit = base_sha
            pair_note = "fixed = merge commit; buggy = base.sha (no parents returned)"
    else:
        # Not merged: the PR head is the proposed fix, the base tip is buggy.
        fixed_commit = head_sha
        buggy_commit = base_sha
        pair_note = f"PR not merged ({pr.get('state')}); fixed = head.sha, buggy = base.sha (proposed)"

    # --- CI logs for the head SHA (best-effort) -------------------------------
    ci_logs: list[CILog] = []
    if include_logs and head_sha:
        runs = soft("workflow runs", lambda: gh.get_json(
            f"/repos/{o}/{r}/actions/runs?head_sha={head_sha}&per_page=50",
            ctx="workflow runs",
        ).get("workflow_runs", []), [])
        # Prioritise failures — that's where numerical crashes surface.
        runs.sort(key=lambda w: 0 if w.get("conclusion") in ("failure", "cancelled") else 1)
        for w in runs[:max_ci_runs]:
            target = downloader.LogTarget(owner=o, repo=r, run_id=str(w["id"]))
            try:
                text = downloader.download_logs(target, token=token)
                if len(text) > max_log_chars:
                    text = text[:max_log_chars] + "\n...[log truncated]..."
                ci_logs.append(CILog(w["id"], w.get("name", ""),
                                     w.get("conclusion") or "", text=text))
            except Exception as e:  # noqa: BLE001
                ci_logs.append(CILog(w["id"], w.get("name", ""),
                                     w.get("conclusion") or "",
                                     note=f"log unavailable: {type(e).__name__}: {e}"))

    return PRBundle(
        ref=ref,
        title=pr.get("title", ""),
        body=pr.get("body") or "",
        state=pr.get("state", ""),
        merged=bool(pr.get("merged")),
        author=(pr.get("user") or {}).get("login", ""),
        head_sha=head_sha,
        base_ref=(pr.get("base") or {}).get("ref", ""),
        labels=[l["name"] for l in pr.get("labels", [])],
        diff=diff,
        files=files,
        commit_messages=commit_messages,
        issue_comments=issue_comments,
        review_bodies=review_bodies,
        review_comments=review_comments,
        linked_issues=linked_issues,
        ci_logs=ci_logs,
        test_commits=test_commits,
        test_files=test_files,
        base_sha=base_sha,
        merge_commit_sha=merge_commit_sha,
        fixed_commit=fixed_commit,
        buggy_commit=buggy_commit,
        commit_pair_note=pair_note,
        warnings=warnings,
    )


def fetch_ci_yaml(ref: PRRef, *, sha: str = "", token: str | None = None,
                  max_files: int = 6, max_chars: int = 6_000) -> list:
    """The project's CI and environment YAML at `sha`, as ``[{path, text}]``.

    The no-miner arm has no mined Q4 telling it how the project builds, so the
    next best evidence is what the project tells its own CI: the workflow files
    say which Python it tests on, which apt and pip packages it installs, and how
    it invokes pytest. A person handed only the pull request would read these too.

    Workflows first (they carry the build), then the conda environment files, and
    at most `max_files` of them — a repository like scipy has dozens of workflows
    and their tail is release tooling, not the build.
    """
    gh = _GitHubClient(token)
    at = f"?ref={sha}" if sha else ""
    found: list = []

    def grab(path: str) -> None:
        if len(found) >= max_files:
            return
        try:
            text = gh.get_text(f"/repos/{ref.owner}/{ref.repo}/contents/{path}{at}",
                               accept="application/vnd.github.raw", ctx=f"yaml {path}")
        except Exception:  # noqa: BLE001 — an absent config is a fact, not an error
            return
        if text.strip():
            found.append({"path": path, "text": text[:max_chars]})

    try:
        listing = gh.get_json(
            f"/repos/{ref.owner}/{ref.repo}/contents/.github/workflows{at}",
            ctx="workflows")
    except Exception:  # noqa: BLE001
        listing = []
    names = sorted(item.get("name", "") for item in (listing or [])
                   if isinstance(item, dict)
                   and str(item.get("name", "")).endswith((".yml", ".yaml")))
    # a "test"/"build"/"ci" workflow before the rest: that is where the build lives
    names.sort(key=lambda n: (not any(k in n.lower()
                                      for k in ("test", "build", "ci", "wheel")), n))
    for name in names:
        grab(f".github/workflows/{name}")
    for extra in ("environment.yml", "environment.yaml", "ci/environment.yml",
                  ".circleci/config.yml", "azure-pipelines.yml"):
        grab(extra)
    return found


def fetch_readme(ref: PRRef, *, sha: str = "", token: str | None = None,
                 max_chars: int = 20_000) -> str:
    """The repository's README at `sha` (its default branch when `sha` is empty).

    The ablation arm that runs WITHOUT the miner has no document corpus to
    retrieve from: its whole view of the project is the pull request plus this
    file. Fetched from the API rather than a clone for the same reason
    `split-patches` is — one request, no 4.8 GB of checkouts left behind — and
    truncated, because a README that runs to tens of thousands of characters is
    a tutorial, and the part that says how the project is built and tested is at
    the top.

    Returns "" when the repository has no README or the request fails: the arm
    is then PR-only, which the caller records rather than treating as fatal.
    """
    gh = _GitHubClient(token)
    path = f"/repos/{ref.owner}/{ref.repo}/readme" + (f"?ref={sha}" if sha else "")
    try:
        text = gh.get_text(path, accept="application/vnd.github.raw", ctx="README")
    except Exception:  # noqa: BLE001 — no README is a fact about the repo, not an error
        return ""
    return text[:max_chars]

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import keyword_filter
# The test detection + per-commit drill live in pr_source so the RAG `analyze`
# path and this crawler share one definition.
from .pr_source import (_GitHubClient, commits_writing_tests, is_test_file,
                        writes_tests)

__all__ = ["PRSummary", "crawl_closed_prs", "is_test_file", "commits_writing_tests",
           "is_dependency_bump", "is_dependency_only", "DEPENDENCY_FILES"]


@dataclass
class PRSummary:
    """One surviving candidate.

    Carries what the RAG stage needs to build a corpus and what reproduction
    needs to find the two revisions — a PR number alone would force every later
    stage back to the API for facts this pass already had in hand.
    """
    number: int
    title: str
    url: str
    author: str
    merged: bool
    state: str
    created_at: str
    closed_at: str
    labels: list[str]
    draft: bool
    repository: str = ""
    description: str = ""
    # the revisions: base is the buggy side, merge_commit the accepted fix
    base_sha: str = ""
    head_sha: str = ""
    merge_commit_sha: str = ""
    # why this PR was kept — the two prescreen halves, recorded separately
    numerical_indicators: list = field(default_factory=list)
    bug_indicators: list = field(default_factory=list)
    changed_files: list = field(default_factory=list)
    #: the subset that could carry the fix — the other half of a FAIL->PASS pair
    source_files: list = field(default_factory=list)
    # the regression oracle: which commits wrote tests, in which files, and which
    # cases they introduced
    test_commits: list = field(default_factory=list)
    test_files: list = field(default_factory=list)
    test_cases: list = field(default_factory=list)

    @property
    def keywords(self) -> list:
        """Backwards-compatible alias for the numerical half of the prescreen."""
        return self.numerical_indicators

    def to_dict(self) -> dict:
        return {
            "repository": self.repository,
            "pr_number": self.number,
            "title": self.title,
            "description": self.description,
            "labels": self.labels,
            "url": self.url,
            "author": self.author,
            "merged": self.merged,
            "state": self.state,
            "draft": self.draft,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "merge_commit_sha": self.merge_commit_sha,
            "numerical_indicators": self.numerical_indicators,
            "bug_indicators": self.bug_indicators,
            "changed_files": self.changed_files,
            "source_files": self.source_files,
            "tests": {
                "test_writing_commits": self.test_commits,
                "test_files": self.test_files,
                "test_cases": self.test_cases,
            },
        }


def parse_date(value) -> "datetime | None":
    """A ``YYYY-MM-DD`` (or full ISO-8601) bound as an aware UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text if "T" in text else f"{text}T00:00:00+00:00")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


#: The funnel, in the order the filters run. Reported so a corpus can state how
#: many PRs each stage removed rather than only how many survived.
FUNNEL_STAGES = ("scanned", "in_date_window", "not_dependency_bump", "merged",
                 "numerical_keywords", "bug_fix_intent", "code_change",
                 "modifies_source", "touches_tests", "test_writing_commit")


def _split_repo(repo: str) -> tuple[str, str]:
    """Parse ``OWNER/REPO`` (also tolerating a trailing ``.git`` or a URL)."""
    repo = repo.strip().removesuffix("/")
    if repo.startswith("http"):
        # https://github.com/OWNER/REPO(.git) -> OWNER/REPO
        repo = repo.split("github.com/", 1)[-1]
    repo = repo.removesuffix(".git")
    parts = [p for p in repo.split("/") if p]
    if len(parts) != 2:
        raise ValueError(f"Expected OWNER/REPO, got {repo!r} (e.g. scipy/scipy).")
    return parts[0], parts[1]


# Automated dependency-bump PRs. They are never numerical bugs (the diff is a
# lockfile or a version pin), but their titles/bodies carry release notes and
# changelogs that readily trip the keyword filter — a 20-PR danfojs prescreen
# came back 17/20 dependabot, one of them matched on "cancel" inside a
# yarn.lock. Dropping them here keeps the expensive per-PR RAG budget on real
# code changes.
_BOT_AUTHORS = frozenset({
    "dependabot", "dependabot[bot]", "dependabot-preview[bot]",
    "renovate[bot]", "renovate-bot", "greenkeeper[bot]",
    "snyk-bot", "pyup-bot", "github-actions[bot]",
})
# `chore(deps):` / `build(deps-dev):` — a conventional-commit dependency scope.
_DEPS_SCOPE = re.compile(r"^\s*\w+\((?:deps|dependencies)[\w-]*\)\s*:", re.I)
# `Bump pathval from 1.1.0 to 1.1.1` — dependabot's bare title form, kept tight
# enough that a prose title like "Bump precision to quad" doesn't match.
_BUMP_TITLE = re.compile(
    r"^\s*(?:\w+(?:\([^)]*\))?\s*:\s*)?bump\s+\S+\s+from\s+v?[\d.]+\s+to\s+v?[\d.]+",
    re.I,
)


#: Manifests and lockfiles. A PR that touches nothing else changed no code, so
#: whatever numerical vocabulary its release notes carry, it fixed no defect.
DEPENDENCY_FILES = re.compile(
    r"(?:^|/)(?:package(?:-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.(?:toml|lock)"
    r"|go\.(?:mod|sum)|requirements[^/]*\.txt|Pipfile(?:\.lock)?|poetry\.lock"
    r"|pyproject\.toml|setup\.cfg|Gemfile(?:\.lock)?|pom\.xml|build\.gradle[^/]*"
    r"|\.pre-commit-config\.yaml|renovate\.json)$", re.I)


#: Changed files that are not the fix: documentation, manifests, CI config,
#: metadata. A PR touching only these has no source change to reproduce against.
_NON_SOURCE = re.compile(
    r"\.(?:md|rst|txt|adoc|png|jpe?g|svg|gif|pdf|cfg|ini|toml|ya?ml|json|lock)$"
    r"|(?:^|/)(?:docs?|documentation|examples?|benchmarks?|\.github|\.circleci"
    r"|\.gitlab)/|(?:^|/)(?:CHANGELOG|CHANGES|NEWS|AUTHORS|LICENSE|COPYING"
    r"|\.gitignore|\.mailmap)", re.I)


def source_files_changed(files) -> list:
    """The files that could contain the fix: not tests, not docs, not manifests.

    A benchmark instance needs BOTH halves — a test that fails on the buggy
    revision, and a source change that makes it pass. A PR that only adds tests
    has nothing to reproduce against, and one that only edits docs cannot have
    fixed anything, so both are rejected here rather than after cloning.
    """
    return [f for f in (files or [])
            if f and not is_test_file(f) and not _NON_SOURCE.search(f)]


def is_dependency_only(files) -> bool:
    """True when every changed file is a manifest or lockfile."""
    names = [f for f in (files or []) if f]
    return bool(names) and all(DEPENDENCY_FILES.search(f) for f in names)


def is_dependency_bump(title: str, author: str) -> bool:
    """True for an automated dependency-bump PR (dependabot/renovate & friends).

    Matches on the bot author *or* the title shape, since a maintainer who
    merges a bump by hand still lands the same non-code diff.
    """
    if author.lower() in _BOT_AUTHORS:
        return True
    return bool(_DEPS_SCOPE.match(title) or _BUMP_TITLE.match(title))


def _pr_files(gh: _GitHubClient, owner: str, name: str, number: int) -> tuple:
    """One paginated call, four answers: every changed file, the test files the
    PR adds lines to, whether it changed nothing but manifests, and which files
    could carry the fix.

    Fetched once per surviving candidate and reused, so the expensive artifacts
    stay behind the cheap filters exactly as the design requires.
    """
    files = gh.paginate(f"/repos/{owner}/{name}/pulls/{number}/files")
    changed = [f.get("filename", "") for f in files if f.get("filename")]
    # level 1 by path, level 2 by construct — a Rust PR writes its tests inside
    # the production source, where no filename rule will find them
    test_files = [f["filename"] for f in files
                  if f.get("additions", 0) > 0
                  and writes_tests(f.get("filename", ""), f.get("patch", ""))]
    return changed, test_files, is_dependency_only(changed), source_files_changed(changed)


def crawl_closed_prs(
    repo: str,
    token: str | None,
    *,
    only_merged: bool = False,
    keyword_prefilter: bool = False,
    require_test_commits: bool = False,
    strict: bool = False,
    require_bug_intent: bool = True,
    require_source_change: bool = True,
    exclude_bumps: bool = True,
    since=None,
    until=None,
    limit: int | None = 20,
    scan_cap: int | None = 1000,
    commit_cap: int = 50,
    stats: dict | None = None,
) -> list[PRSummary]:
    """List a repo's closed PRs, newest-first, up to `limit` after filtering.

    When a filter (`only_merged` / `keyword_prefilter` / `require_test_commits`)
    is active we page through up to `scan_cap` closed PRs to find `limit`
    matches; otherwise we stop as soon as `limit` PRs are fetched.

    `since` / `until` bound the window by PR creation date (``YYYY-MM-DD``).
    Because the listing is newest-first, `since` also STOPS the scan as soon as
    it is passed: a dated corpus costs one page per window, not `scan_cap` pages.
    Pass `limit=None` to take every PR in the window.

    `stats`, when given, is filled with the funnel — how many PRs each stage saw
    and how many it passed — so a corpus can report its own selectivity.

    `exclude_bumps` (on by default) drops automated dependency-bump PRs —
    dependabot/renovate authors, or a `chore(deps):`/`Bump X from A to B` title.
    Their changelog-laden bodies trip the keyword filter but their diffs are
    lockfiles, so they can crowd out every real candidate; pass False to list a
    repo's closed PRs verbatim.

    The numerical prescreen keeps a PR on *any* keyword hit by default; `strict`
    instead requires the filter's weighted score to clear its threshold (the
    same call `--keyword` classification uses), so a lone weak term like
    "exception" no longer qualifies.

    `require_test_commits` keeps only PRs with at least one commit that writes
    test cases, and *implies* both the numerical keyword filter and `only_merged`
    — so it selects merged, numerical PRs that ship a reproducing test (a
    benchmark instance needs the fix that actually landed). It reads each
    surviving PR's commits and their files, so run it with a `GITHUB_TOKEN`.
    """
    owner, name = _split_repo(repo)
    gh = _GitHubClient(token)
    slug = f"{owner}/{name}"

    # The test-commit gate is about *numerical* PRs that write tests, so it
    # turns the (cheap, title/body) numerical keyword prescreen on implicitly.
    numeric_gate = keyword_prefilter or require_test_commits
    # A benchmark instance needs a landed fix, so the test-commit gate also
    # requires the PR to be merged.
    merged_gate = only_merged or require_test_commits

    since_dt, until_dt = parse_date(since), parse_date(until)
    counts = {k: 0 for k in FUNNEL_STAGES}
    if stats is not None:
        stats.update(counts)
        stats["window"] = {"since": str(since or ""), "until": str(until or "")}

    cap = scan_cap if (merged_gate or numeric_gate or since_dt) else (limit or scan_cap)
    # reverse chronological: newest PRs first, by creation rather than by last
    # touch, so a years-old PR someone commented on yesterday does not lead.
    # Read lazily so `since` stops the scan instead of filtering what was already
    # paid for.
    raw = gh.paginate_iter(
        f"/repos/{owner}/{name}/pulls?state=closed&sort=created&direction=desc",
        cap=cap,
    )

    out: list[PRSummary] = []
    stopped = "exhausted"
    for pr in raw:
        counts["scanned"] += 1
        created = parse_date(pr.get("created_at"))
        if since_dt and created and created < since_dt:
            stopped = "date_boundary"
            break              # newest-first: everything after this is older too
        if cap is not None and counts["scanned"] >= cap:
            # the window was NOT exhausted — say so, or a corpus silently
            # describes the newest `cap` PRs while claiming the whole window
            stopped = "scan_cap"
            break
        if until_dt and created and created > until_dt:
            continue
        counts["in_date_window"] += 1
        title = pr.get("title", "") or ""
        labels = [l["name"] for l in pr.get("labels", [])]
        merged = pr.get("merged_at") is not None

        # --- 2. dependency/bump filter (free: title + author) ----------------
        if exclude_bumps and is_dependency_bump(
                title, (pr.get("user") or {}).get("login", "") or ""):
            continue
        counts["not_dependency_bump"] += 1

        # --- 3. merged-PR filter (free: the listing says so) -----------------
        if merged_gate and not merged:
            continue
        counts["merged"] += 1

        # --- 4. numerical relevance AND bug-fixing intent (free: title+body) -
        numerical: list = []
        bugs: list = []
        if numeric_gate:
            hits = keyword_filter.scan(f"{title}\n{pr.get('body') or ''}")
            if not hits:
                continue
            # Loose (default): any keyword hit qualifies. Strict: the distinct
            # keywords must sum past the filter's threshold (same rule as the
            # --keyword classifier), so a lone weak "exception" is dropped.
            if strict and sum(h.weight for h in hits) < keyword_filter.DEFAULT_THRESHOLD:
                continue
            counts["numerical_keywords"] += 1
            numerical = [h.keyword for h in hits]
            # Numerical vocabulary alone also fires on feature additions and
            # release notes; the candidate must additionally look like a FIX.
            bugs = keyword_filter.bug_indicators(title, labels)
            if require_bug_intent and not bugs:
                continue
            counts["bug_fix_intent"] += 1

        # --- 5. test-modification filter (costs one files call, then commits) -
        number = pr.get("number", 0)
        changed_files: list = []
        source_files: list = []
        test_commits: list = []
        test_files: list = []
        test_cases: list = []
        if require_test_commits:
            changed_files, pr_test_files, deps_only, source_files = _pr_files(
                gh, owner, name, number)
            # a manifest-only PR changed no code, whatever its notes say
            if deps_only and exclude_bumps:
                continue
            counts["code_change"] += 1
            # the fix half: a PR that only adds tests leaves nothing to reproduce
            if require_source_change and not source_files:
                continue
            counts["modifies_source"] += 1
            if not pr_test_files:      # nothing test-shaped: skip the per-commit drill
                continue
            counts["touches_tests"] += 1
            test_commits = commits_writing_tests(gh, owner, name, number, cap=commit_cap)
            if not test_commits:  # PR touches tests, but no single commit wrote one
                continue
            counts["test_writing_commit"] += 1
            test_files = sorted({f for tc in test_commits for f in tc["test_files"]})
            test_cases = sorted({c for tc in test_commits for c in tc.get("test_cases", [])})

        out.append(PRSummary(
            number=number,
            title=title,
            url=pr.get("html_url", ""),
            author=(pr.get("user") or {}).get("login", ""),
            merged=merged,
            state=pr.get("state", "closed"),
            created_at=pr.get("created_at", "") or "",
            closed_at=pr.get("closed_at", "") or "",
            labels=labels,
            draft=bool(pr.get("draft")),
            repository=slug,
            description=(pr.get("body") or "")[:20000],
            base_sha=((pr.get("base") or {}).get("sha") or ""),
            head_sha=((pr.get("head") or {}).get("sha") or ""),
            merge_commit_sha=pr.get("merge_commit_sha") or "",
            numerical_indicators=numerical,
            bug_indicators=bugs,
            changed_files=changed_files,
            source_files=source_files,
            test_commits=test_commits,
            test_files=test_files,
            test_cases=test_cases,
        ))
        if limit and len(out) >= limit:
            stopped = "limit"
            break
    if stats is not None:
        # A gate that never ran has no count — reporting it as 0 reads as "this
        # stage rejected everything", which is the opposite of what happened.
        inactive = set()
        if not exclude_bumps:
            inactive.add("not_dependency_bump")
        if not merged_gate:
            inactive.add("merged")
        if not numeric_gate:
            inactive |= {"numerical_keywords", "bug_fix_intent"}
        elif not require_bug_intent:
            inactive.add("bug_fix_intent")
        if not require_test_commits:
            inactive |= {"code_change", "modifies_source", "touches_tests",
                         "test_writing_commit"}
        elif not require_source_change:
            inactive.add("modifies_source")
        stats["scan_stopped"] = stopped
        stats.update({k: v for k, v in counts.items() if k not in inactive})
        for key in inactive:      # seeded with zeros at the start; remove them
            stats.pop(key, None)
        stats["stages_not_run"] = sorted(inactive)
        stats["kept"] = len(out)
    return out

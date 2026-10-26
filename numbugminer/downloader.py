from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

import requests

API_ROOT = "https://api.github.com"
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z\s")

_RUN_URL = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/actions/runs/(?P<run>\d+)"
    r"(?:/job/(?P<job>\d+))?"
)
_SHORTHAND = re.compile(r"^(?P<owner>[^/]+)/(?P<repo>[^/#]+)#(?P<run>\d+)$")


@dataclass(frozen=True)
class LogTarget:
    owner: str
    repo: str
    run_id: str | None = None
    job_id: str | None = None

    @property
    def slug(self) -> str:
        ident = self.job_id or self.run_id or "unknown"
        return f"{self.owner}_{self.repo}_{ident}"


def parse_target(ref: str, repo: str | None = None, job_id: str | None = None) -> LogTarget:
    """Turn a user-supplied reference into a LogTarget."""
    m = _RUN_URL.search(ref)
    if m:
        repo_name = m["repo"]
        if repo_name.endswith(".git"):
            repo_name = repo_name[: -len(".git")]
        return LogTarget(
            owner=m["owner"],
            repo=repo_name,
            run_id=m["run"],
            job_id=m["job"] or job_id,
        )

    m = _SHORTHAND.match(ref)
    if m:
        return LogTarget(owner=m["owner"], repo=m["repo"], run_id=m["run"], job_id=job_id)

    # Bare id -> needs an explicit --repo.
    if ref.isdigit():
        if not repo or "/" not in repo:
            raise ValueError(
                "A bare id needs --repo OWNER/REPO (e.g. --repo numpy/numpy)."
            )
        owner, name = repo.split("/", 1)
        # Ambiguous whether a bare id is a run or a job; treat as run by default.
        return LogTarget(owner=owner, repo=name, run_id=ref, job_id=job_id)

    raise ValueError(f"Could not parse job reference: {ref!r}")


def _clean(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    return "\n".join(_TIMESTAMP.sub("", line) for line in text.splitlines())


def download_logs(target: LogTarget, token: str | None, timeout: int = 60) -> str:
    """Return the concatenated, timestamp-stripped log text for the target."""
    if target.job_id:
        url = f"{API_ROOT}/repos/{target.owner}/{target.repo}/actions/jobs/{target.job_id}/logs"
    elif target.run_id:
        url = f"{API_ROOT}/repos/{target.owner}/{target.repo}/actions/runs/{target.run_id}/logs"
    else:
        raise ValueError("LogTarget needs a run_id or job_id.")

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    resp = requests.get(url, headers=headers, timeout=timeout)
    if resp.status_code == 404:
        raise RuntimeError(
            f"404 for {url}. Logs may have expired (>90 days), the run/job id is "
            "wrong, or the token lacks actions:read on this repo."
        )
    if resp.status_code == 401:
        raise RuntimeError("401 Unauthorized — set a valid GITHUB_TOKEN with actions:read.")
    resp.raise_for_status()

    content = resp.content
    # A single-job logs endpoint returns plain text; the run endpoint returns a zip.
    if content[:2] == b"PK":  # zip magic
        parts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in sorted(zf.namelist()):
                if name.endswith(".txt"):
                    parts.append(f"===== {name} =====\n{_clean(zf.read(name))}")
        return "\n\n".join(parts)
    return _clean(content)

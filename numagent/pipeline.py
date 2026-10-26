from __future__ import annotations

from typing import Optional

from . import detect as _detect
from . import steps
from . import workspace
from .models import RunReport


def run_pipeline(
    src: str,
    workdir: Optional[str] = None,
    copy: bool = True,
    do_build: bool = True,
    do_test: bool = True,
    timeout_s: float = 900.0,
) -> RunReport:
    """Run the harness against a repo (URL or local path).

    copy=False runs in place on a local path (faster, but mutates the tree).
    """
    if copy or workspace._is_url(src):
        repo_path = workspace.prepare(src, workdir)
    else:
        repo_path = src

    det = _detect.detect(repo_path)
    report = RunReport(repo_path=repo_path, detect=det)

    if do_build:
        report.build = steps.build(repo_path, det, timeout_s=timeout_s)
        # If build hard-failed (not merely skipped), still attempt tests only
        # when they don't depend on build artifacts (pip installs, pytest).
        if report.build and not report.build.success and not report.build.skipped:
            if det.build_system.value not in ("pip",):
                do_test = do_test and report.build.skipped

    if do_test:
        report.test = steps.test(repo_path, det, timeout_s=timeout_s)

    return report

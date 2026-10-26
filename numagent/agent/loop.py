from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .. import workspace
from .llm.base import LLMClient
from .tools import ToolRegistry

SYSTEM_PROMPT = """\
You are NumAgent, an autonomous engineer that builds and tests numerical software \
repositories (Python, C/C++, Fortran).

Your job for the given repository:
  1. Detect its build system (use detect_build_system, and inspect files as needed).
  2. Build it.
  3. Run its test suite.
  4. Call report_result exactly once with verdict "pass" (built and tests passed) or
     "fail" (build failed, tests failed, or no tests could be run), and a concise summary.

Guidance:
  - Prefer the high-level build/test tools; drop to run_command for inspection or
    steps they don't cover.
  - If a toolchain is missing (e.g. no gfortran), don't try to work around it —
    report the limitation and fail.
  - Be efficient: don't repeat a tool call that already gave you the answer.
  - You are operating autonomously; no human is available to answer questions.
"""

NUMERIC_GUIDANCE = """

Numerical correctness (beyond does-it-pass):
  This is numerical software, so also validate that the numbers are RIGHT when you
  can. You have extra tools:
    - check_numeric_close — compare computed values to a reference within tolerance.
    - check_convergence — confirm a method converges at its expected order.
    - check_reproducibility — run a command several times; catch nondeterminism.
  Use them when the repo exposes a way to produce numbers (an example script, a
  CLI, a documented expected result). If a numerical check fails, that is a real
  defect even if the unit tests pass — weigh it in your verdict and summary.
"""

DOCKER_PROMPT = """\
You are NumAgent, an autonomous engineer that produces a reproducible Docker-based \
build+test for a numerical software repository (Python, C/C++, Fortran).

Your deliverables are two files you AUTHOR yourself:
  - .numagent/Dockerfile  — installs the toolchain the repo needs and COPYs the
    source in. The build context is the repo root; end with:
        CMD ["bash", ".numagent/run.sh"]
  - .numagent/run.sh       — a bash script (start with `set -euo pipefail`) that
    builds the project and runs its test suite.

Workflow:
  1. Inspect the repo (detect_build_system, list_dir, read_file) to learn the
     language, build system, and any system dependencies.
  2. You MAY call suggest_docker_draft for a reference starting point, but you are
     responsible for the final files — write them with write_file.
  3. Call run_in_docker to build the image and run the tests inside the container.
  4. If the image build or the tests fail, read the logs, fix the Dockerfile /
     run.sh / (if genuinely a code bug) the source, and run_in_docker again.
     Iterate until the container builds and the tests pass.
  5. Call report_result exactly once: "pass" if the containerized suite is green,
     else "fail", summarizing what the Dockerfile/run.sh do.

Rules:
  - The container is the environment — install missing tools there (apt-get,
    pip, downloading gfortran/fpm), do NOT rely on the host toolchain.
  - Keep the image lean: a slim base plus only the packages the repo needs.
  - Fix the implementation, never the tests, if you touch source at all.
  - You are operating autonomously; no human is available to answer questions.
"""

FIX_PROMPT = """\
You are NumAgent, an autonomous engineer that builds, tests, AND repairs numerical \
software repositories (Python, C/C++, Fortran).

Your job for the given repository:
  1. Detect its build system, then build it and run its test suite.
  2. If the build or tests FAIL, diagnose the root cause by reading the relevant
     source, then fix it with edit_file (targeted) or write_file (new/rewrite).
  3. Re-run build and test after each change. Iterate until the tests pass or you
     are confident the failure is unfixable (e.g. a missing toolchain, or the test
     itself encodes a contradictory requirement).
  4. Call report_result exactly once: "pass" if the suite ends green, else "fail".
     In the summary, describe the root cause and the fix you applied.

Rules for fixing:
  - Fix the IMPLEMENTATION, never the tests. Do NOT edit files under tests/ (or
    test_*.py / *_test files) to make a failing test pass — that is cheating.
    Only touch a test if it has an obvious, unrelated typo that breaks collection.
  - Make the smallest change that fixes the actual bug. No refactors, no new
    features, no speculative error handling.
  - After every edit, re-run test to confirm the change helped and didn't regress
    anything else. Trust the test results, not your assumptions.
  - If a toolchain is missing, you cannot fix that — report and fail.
  - You are operating autonomously; no human is available to answer questions.
"""


@dataclass
class AgentResult:
    verdict: str
    summary: str
    iterations: int
    repo_path: str
    changes: List[Dict[str, str]] = field(default_factory=list)
    transcript: List[Dict[str, Any]] = field(default_factory=list)


def run_agent(
    src: str,
    client: LLMClient,
    workdir: Optional[str] = None,
    copy: bool = True,
    fix: bool = False,
    docker: bool = False,
    numeric: bool = False,
    max_iterations: int = 25,
    timeout_s: float = 900.0,
    on_event: Optional[Callable[[str, Any], None]] = None,
    context: Optional[str] = None,
    ref: Optional[str] = None,
    test_patch: Optional[str] = None,
) -> AgentResult:
    """Drive `client` to build and test the repo at `src` (URL or local path).

    fix=True enables the auto-fix loop: the agent gets edit_file/write_file and is
    prompted to repair failures and iterate until tests pass.

    docker=True enables the containerized mode: the agent authors .numagent/
    Dockerfile + run.sh, builds and runs them under Docker, and iterates.

    context is an optional task briefing (e.g. the miner's issue+PR bug report)
    injected into the initial user message so the agent knows which bug to
    reproduce and fix. ref, with a git URL, checks out that exact commit (e.g.
    the bug's "buggy" commit from the miner).

    on_event(kind, payload) is an optional progress hook; kinds: 'assistant',
    'tool_call', 'tool_result'.
    """
    def emit(kind, payload):
        if on_event:
            on_event(kind, payload)

    if ref and workspace._is_url(src):
        repo_path = workspace.prepare(src, workdir, ref=ref)
    else:
        repo_path = workspace.prepare(src, workdir) if (copy or workspace._is_url(src)) else src

    # --- Preprocessing: expose the bug before the agent starts ----------------
    # Apply ONLY the PR's test-file hunks onto the buggy checkout so the
    # reproducing test is present and failing. This is the fail->pass target the
    # agent must satisfy; the fix itself is withheld.
    if test_patch and test_patch.strip():
        applied = workspace.apply_patch(repo_path, test_patch)
        emit("preprocess", f"reproducing test patch: "
                           f"{'applied' if applied else 'FAILED to apply'}")
    # docker builds pull base images — give them more headroom.
    eff_timeout = max(timeout_s, 1800.0) if docker else timeout_s
    # docker mode needs to author files, so it implies edit tools.
    tools = ToolRegistry(repo_path, timeout_s=eff_timeout,
                         allow_edit=fix or docker, allow_docker=docker,
                         allow_numeric=numeric)
    schemas = tools.schemas()

    if docker:
        prompt, task = DOCKER_PROMPT, "Produce a Docker-based build+test for"
    elif fix:
        prompt, task = FIX_PROMPT, "Build, test, and fix"
    else:
        prompt, task = SYSTEM_PROMPT, "Build and test"
    if numeric:
        prompt = prompt + NUMERIC_GUIDANCE

    user_msg = f"{task} the repository located at: {repo_path}"
    if context:
        # The miner's issue+PR data: what bug the agent should reproduce and fix.
        # It describes the problem, not the solution — find the fix yourself.
        user_msg += (
            "\n\nThis repository is checked out at the state that still contains a "
            "reported numerical bug. Reproduce it, then fix the implementation so "
            "the bug is resolved and the test suite passes. Do not merely silence "
            "warnings. The bug report follows.\n\n"
            "===== BUG REPORT (from issue + PR) =====\n"
            f"{context}\n"
            "========================================"
        )
    if test_patch and test_patch.strip():
        # The reproducing test was applied in preprocessing; it currently fails.
        user_msg += (
            "\n\nA reproducing test has ALREADY been added to the test suite and "
            "currently FAILS on this buggy checkout. Your goal is to make it pass "
            "by fixing the implementation — do NOT edit or delete the test."
        )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_msg},
    ]

    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        turn = client.complete(messages, schemas)

        if turn.text:
            emit("assistant", turn.text)

        # Record the assistant turn (text + any tool calls) in neutral form.
        messages.append({"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls})

        if not turn.wants_tools:
            # Model stopped without reporting — nudge it once, then give up.
            if iterations >= max_iterations:
                break
            messages.append({
                "role": "user",
                "content": "If you are finished, call report_result. Otherwise continue.",
            })
            continue

        for tc in turn.tool_calls:
            emit("tool_call", tc)
            result = tools.call(tc.name, tc.arguments)
            emit("tool_result", (tc.name, result))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.name,
                "content": result,
            })

        if tools.finished is not None:
            return AgentResult(
                verdict=tools.finished["verdict"],
                summary=tools.finished["summary"],
                iterations=iterations,
                repo_path=repo_path,
                changes=tools.changes,
                transcript=messages,
            )

    return AgentResult(
        verdict="fail",
        summary=f"agent did not reach a verdict within {max_iterations} iterations",
        iterations=iterations,
        repo_path=repo_path,
        changes=tools.changes,
        transcript=messages,
    )

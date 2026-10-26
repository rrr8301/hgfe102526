from __future__ import annotations

from pathlib import Path

from . import docker_gen
from .models import DetectResult, StepResult
from .shell import parse_pytest_counts, run


def build_and_run(
    repo_path: str,
    image_tag: str,
    dockerfile_rel: str = ".numagent/Dockerfile",
    timeout_s: float = 1800.0,
) -> StepResult:
    """docker build (context = repo root) then docker run --rm. Returns a
    StepResult whose commands are [build] or [build, run]."""
    df = Path(repo_path) / dockerfile_rel
    if not df.is_file():
        return StepResult("docker", success=False, skipped=True,
                          summary=f"no Dockerfile at {dockerfile_rel} — write one first")

    build = run(["docker", "build", "-f", dockerfile_rel, "-t", image_tag, "."],
                cwd=repo_path, timeout_s=timeout_s)
    cmds = [build]
    success = False
    if build.ok:
        runc = run(["docker", "run", "--rm", image_tag], cwd=repo_path, timeout_s=timeout_s)
        cmds.append(runc)
        success = runc.ok

    step = StepResult("docker", success=success, commands=cmds,
                      summary=f"docker build + run ({image_tag})")
    if len(cmds) > 1:
        step.tests_passed, step.tests_failed = parse_pytest_counts(cmds[-1].stdout + cmds[-1].stderr)
    return step


def dockerized_pipeline(
    repo_path: str, det: DetectResult, timeout_s: float = 1800.0
) -> Tuple[StepResult, docker_gen.DockerArtifacts]:
    """No-LLM path: generate the draft, write it to .numagent/, build + run."""
    arts = docker_gen.generate(repo_path, det)
    docker_gen.write(arts, repo_path)
    step = build_and_run(repo_path, arts.image_tag, timeout_s=timeout_s)
    return step, arts

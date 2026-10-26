from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

BENCH = "/benchmark"

BUILD_DEPS = ("build-essential", "gfortran", "git", "pkg-config", "cmake",
              "ninja-build", "libopenblas-dev", "liblapack-dev", "patch")

_DOCKERFILE = '''# {instance_id} — NumBugBench instance, REBUILD FROM SOURCE
#
# Unlike the Dockerfile inside the published image, this one reconstructs the
# instance: it clones both revisions, applies the developer test, installs each
# revision's frozen dependencies and builds the subject from its checkout.
#
# The final build step runs `validate.sh`, which exits non-zero unless
#     T_dev(buggy) = FAIL   and   T_dev(fixed) = PASS
# so THIS IMAGE CANNOT BE BUILT UNLESS THE F2P REPRODUCES. A dependency that
# drifts into an environment where the bug does not manifest breaks the build
# rather than yielding a silently wrong instance.
#
#   docker build -t {instance_id}:rebuilt .
#   docker run --rm {instance_id}:rebuilt validate
#
# PR: {reference}
FROM python:{python_tag}

ENV DEBIAN_FRONTEND=noninteractive PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update \\
 && apt-get install -y --no-install-recommends {deps} \\
 && rm -rf /var/lib/apt/lists/*

# ---- the two revisions, at the commits the reproduction validated ----------
ARG REPO={repo_url}
ARG BUGGY={buggy_sha}
ARG FIXED={fixed_sha}
RUN git clone --no-checkout --filter=blob:none "$REPO" {buggy_dir} \\
 && git -C {buggy_dir} checkout --detach "$BUGGY" \\
 && git -C {buggy_dir} submodule update --init --recursive --depth 1 || true
RUN git clone --no-checkout --filter=blob:none "$REPO" {fixed_dir} \\
 && git -C {fixed_dir} checkout --detach "$FIXED" \\
 && git -C {fixed_dir} submodule update --init --recursive --depth 1 || true

# ---- the developer test, on BOTH revisions ---------------------------------
# The test is the oracle, so it must be identical on the two sides; only the
# source under it differs. `git apply` is tried first, then `patch`, because a
# test file that does not yet exist on the buggy side is an add, not a change.
COPY developer_test.patch /tmp/developer_test.patch
RUN for d in {buggy_dir} {fixed_dir}; do \\
      cd "$d" && \\
      ( git apply --3way /tmp/developer_test.patch \\
        || git apply /tmp/developer_test.patch \\
        || patch -p1 --forward < /tmp/developer_test.patch \\
        || true ); \\
    done

# ---- one frozen environment per revision -----------------------------------
# The subject itself is stripped from the lockfile: a freeze records it as a
# local path (`{package} @ file:///workspace/...`), which pip cannot resolve,
# and it must come from the checkout so that each side builds ITS OWN source.
# Only the exact name is dropped: a bare `^{package}` prefix also strips
# companions such as `astropy-iers-data`, which astropy then fails to import.
#
# Build ISOLATION stays on for the first two attempts. Turning it off — which
# a compiled project sometimes needs, to build against the pinned numpy rather
# than a fresh one — starves a project of its build-time requirements:
# mpmath#1107 needs setuptools_scm to write `mpmath/_version.py`, which is
# generated rather than committed, and without it `import mpmath` fails and the
# build correctly refused to finish. So no-isolation is the LAST resort.
COPY env/ {bench}/env/
RUN set -eux; for rev in buggy fixed; do \\
      d=/workspace/$rev; \\
      python -m venv "$d/.venv"; \\
      grep -vEi '^({package}[[:space:]]*(==|@|$)|numagent[_-]|-e |#|$)' {bench}/env/$rev/requirements.lock \\
        | grep -vE '@ file://' > /tmp/$rev.lock; \\
      "$d/.venv/bin/pip" install --no-input -q -r /tmp/$rev.lock; \\
      "$d/.venv/bin/pip" install --no-input -q -e "$d" --no-deps \\
        || "$d/.venv/bin/pip" install --no-input -q "$d" --no-deps \\
        || "$d/.venv/bin/pip" install --no-input -q -e "$d" --no-deps \\
             --no-build-isolation; \\
    done

COPY scripts/ {bench}/scripts/
COPY metadata.json {bench}/metadata.json
RUN mkdir -p {bench}/candidate && chmod +x {bench}/scripts/*.sh

# ---- the guarantee ---------------------------------------------------------
RUN {bench}/scripts/validate.sh

ENTRYPOINT ["{bench}/scripts/entrypoint.sh"]
CMD ["validate"]
'''


def _in_image(image: str, command: str) -> str:
    r = subprocess.run(["docker", "run", "--rm", "--entrypoint", "bash", image,
                        "-c", command], capture_output=True, text=True)
    return r.stdout


def _python_tag(interpreter: str) -> str:
    m = re.search(r"(\d+)\.(\d+)", interpreter or "")
    return f"{m.group(1)}.{m.group(2)}" if m else "3.11"


def write_context(image: str, out_dir: Path, test_patch: Path | None = None) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads(_in_image(image, f"cat {BENCH}/metadata.json") or "{}")
    if not meta:
        raise RuntimeError(f"{image}: no {BENCH}/metadata.json — not a packaged instance")
    project = str(meta.get("project") or "")
    package = project.split("/")[-1].lower() or "unknown"
    interpreter = ((meta.get("environment") or {}).get("python")
                   or _in_image(image, f"cat {BENCH}/env/fixed/interpreter.txt").strip())

    (out / "env" / "buggy").mkdir(parents=True, exist_ok=True)
    (out / "env" / "fixed").mkdir(parents=True, exist_ok=True)
    for rev in ("buggy", "fixed"):
        lock = _in_image(image, f"cat {BENCH}/env/{rev}/requirements.lock")
        (out / "env" / rev / "requirements.lock").write_text(lock, encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    scripts = out / "scripts"
    scripts.mkdir(exist_ok=True)
    listing = _in_image(image, f"ls {BENCH}/scripts").split()
    for name in listing:
        body = _in_image(image, f"cat {BENCH}/scripts/{name}")
        (scripts / name).write_text(body, encoding="utf-8")

    if test_patch and Path(test_patch).is_file():
        shutil.copyfile(test_patch, out / "developer_test.patch")
    else:
        (out / "developer_test.patch").write_text("", encoding="utf-8")

    (out / "Dockerfile").write_text(_DOCKERFILE.format(
        instance_id=meta.get("instance_id") or package,
        reference=meta.get("reference") or "-",
        python_tag=_python_tag(interpreter),
        deps=" ".join(BUILD_DEPS),
        repo_url=f"https://github.com/{project}.git",
        buggy_sha=meta.get("buggy_revision") or "",
        fixed_sha=meta.get("fixed_revision") or "",
        buggy_dir="/workspace/buggy", fixed_dir="/workspace/fixed",
        bench=BENCH, package=re.escape(package)), encoding="utf-8")
    return {"instance": meta.get("instance_id"), "dir": str(out),
            "python": _python_tag(interpreter), "package": package,
            "scripts": len(listing)}

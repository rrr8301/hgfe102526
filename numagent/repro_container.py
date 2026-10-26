from __future__ import annotations

import base64
import dataclasses
import json
import uuid
from pathlib import Path

from . import shell
from . import repro_probe_langs as langs
from .models import CommandResult

PROBE_MARKER = "__NUMAGENT_PROBE__"


class Container:
    """A long-lived Docker container the agent runs commands in.

    State a command installs (pip/apt) persists across ``run`` calls; cwd and
    env reset each call (each run is a fresh login shell).

    Commands run through a login shell so that a toolchain the agent installs
    (rustup, nvm, conda) is on PATH afterwards — but a login shell re-sources
    /etc/profile, which throws away the PATH the IMAGE set. That is how a Rust
    image ends up without ``cargo``. The image's own PATH is therefore read once
    at start and put back in front of every command.
    """

    def __init__(self, image: str, name: str | None = None, on_output=None):
        self.image = image
        self.name = name or f"repro_{uuid.uuid4().hex[:10]}"
        self.started = False
        self.image_path = ""            # PATH as the image defines it
        # optional live sink: on_output(stream, line) for every line a command
        # prints, as it prints it. Capture is unaffected.
        self.on_output = on_output

    def start(self) -> None:
        shell.run(["docker", "rm", "-f", self.name])
        r = shell.run(["docker", "run", "-d", "--name", self.name, self.image,
                       "sleep", "infinity"])
        if r.exit_code != 0:
            raise RuntimeError(f"docker run failed: {r.stderr.strip()[:300]}")
        self.started = True
        # read without a shell, so this is the image's ENV PATH, not a login one
        probe = shell.run(["docker", "exec", self.name, "printenv", "PATH"])
        self.image_path = (probe.stdout or "").strip() if probe.exit_code == 0 else ""

    def copy_in(self, host_dir: Path, dest: str) -> None:
        shell.run(["docker", "exec", self.name, "mkdir", "-p", dest])
        # trailing /. copies directory *contents* into dest
        r = shell.run(["docker", "cp", f"{host_dir}/.", f"{self.name}:{dest}"])
        if r.exit_code != 0:
            raise RuntimeError(f"docker cp failed: {r.stderr.strip()[:300]}")

    def run(self, command: str, workdir: str = "/workspace",
            timeout_s: float = 1800.0) -> CommandResult:
        """Run ``command`` in the container; stdout/stderr/exit code are kept
        separate (a timeout yields ``timed_out=True`` and exit code 124)."""
        prelude = f'export PATH="{self.image_path}:$PATH"; ' if self.image_path else ""
        # A revision that had to be BUILT owns a virtualenv holding its own build
        # of the package. Putting it first on PATH is what keeps the two
        # revisions apart: `python`, `pip` and `pytest` then mean this revision's,
        # for the agent's commands, the harness's, and the probes alike. Absent a
        # .venv (a pure-Python repo, or any other directory) nothing changes.
        prelude += ('if [ -x "$PWD/.venv/bin/python" ]; then '
                    'PATH="$PWD/.venv/bin:$PATH"; fi; ')
        # A revision pinned to its era carries a constraints file; exporting it
        # makes EVERY pip in that revision honour it — the harness's installs
        # and the agent's own — so nothing upgrades numpy back to today's.
        prelude += ('if [ -f "$PWD/.numagent-constraints.txt" ]; then '
                    'export PIP_CONSTRAINT="$PWD/.numagent-constraints.txt"; fi; ')
        r = shell.run(["docker", "exec", "-w", workdir, self.name, "bash", "-lc",
                       prelude + command],
                      timeout_s=timeout_s, on_line=self.on_output)
        # the record keeps the command as asked for, not as wrapped
        return dataclasses.replace(r, command=command, cwd=workdir)

    def stop(self) -> None:
        if self.started:
            shell.run(["docker", "rm", "-f", self.name])
            self.started = False

    def commit(self, image: str, changes: list | None = None,
               timeout_s: float = 1800.0) -> CommandResult:
        """Snapshot this container's filesystem as `image`.

        Everything the session installed and built is kept, so the resulting
        image reruns without a network. `changes` are Dockerfile directives
        (``CMD …``, ``WORKDIR …``, ``LABEL …``) stamped onto the new image.
        """
        args = ["docker", "commit"]
        for change in changes or []:
            args += ["--change", change]
        args += [self.name, image]
        return shell.run(args, timeout_s=timeout_s)


def write_file(container: Container, path: str, text: str) -> None:
    """Write `text` to `path` in the container via base64 (no quoting hazards)."""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    container.run(f"echo {b64} | base64 -d > {path}", timeout_s=60)


def parse_marker_json(out: str, marker: str) -> dict | None:
    """Pull the JSON object emitted after `marker` out of program output."""
    if marker not in out:
        return None
    tail = out.rsplit(marker, 1)[1]
    for line in tail.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def strip_marker(out: str, marker: str = PROBE_MARKER) -> str:
    """The program's own output, with the harness's payload line removed."""
    return out.split(marker, 1)[0] if marker in out else out


# --------------------------------------------------------------------------- #
# the numerical probe
# --------------------------------------------------------------------------- #
# The recording API and the marker-delimited JSON payload are the same in every
# language (see :mod:`numagent.repro_probe_langs`); only the harness that offers
# them differs. Interpreting the payload — NaN/Inf state, residuals, convergence,
# error metrics — is :func:`numagent.repro_compare.analyze_probe`'s job, so one
# deterministic oracle grades a sympy probe and a gonum probe identically.
def detect_probe_language(container: Container, workdir: str) -> str:
    """Which language the revision in `workdir` is written in ('' when unsure)."""
    res = container.run(langs.DETECT_COMMAND, workdir=workdir, timeout_s=60)
    return langs.language_from_markers(res.stdout)


def container_numerical_probe(container: Container, code: str, *, workdir: str,
                              timeout_s: float = 300.0, language: str = "python",
                              extra_flags: str = "", package=None) -> dict:
    """Run `code` inside `workdir` as a probe, under `language`'s harness.

    Returns the RAW capture — status, recorded values, iteration trace, warnings,
    traceback, streams and runtime — plus the language actually used. A language
    with no harness, or a missing toolchain, comes back as ``harness_error``:
    unusable, never scored as a numerical defect.
    """
    try:
        plan = langs.plan_probe(language, code, marker=PROBE_MARKER,
                               workdir=workdir, extra_flags=extra_flags,
                               package=package)
    except ValueError as e:
        return {"exit_code": 127, "timed_out": False, "runtime_s": 0.0,
                "stdout": "", "stderr": str(e), "status": "harness_error",
                "exception_type": "UnsupportedLanguage", "traceback": str(e),
                "records": {}, "iterations": [], "warnings": [], "plan": None,
                "language": langs.canonical(language) or language}

    if plan.dirs:
        container.run("mkdir -p " + " ".join(plan.dirs), timeout_s=60)
    for path, text in plan.files.items():
        write_file(container, path, text)

    res = container.run(plan.command, workdir=workdir, timeout_s=timeout_s)
    payload = parse_marker_json(res.stdout + "\n" + res.stderr, PROBE_MARKER)
    raw = {"exit_code": res.exit_code, "timed_out": res.timed_out,
           "runtime_s": res.duration_s, "language": plan.language,
           "stdout": strip_marker(res.stdout), "stderr": res.stderr,
           # the exact harness files + command this probe ran, so the probe can
           # be replayed later (that is what packaging bakes into the image)
           "plan": {"command": plan.command, "files": dict(plan.files),
                    "dirs": list(plan.dirs), "workdir": workdir}}
    if payload is None:
        # the harness never reported: no toolchain, a compile error, or a kill
        combined = res.stderr or res.stdout
        exc = ""
        if langs.NO_TOOLCHAIN in (res.stdout + res.stderr):
            exc = "MissingToolchain"
        elif langs.looks_like_build_failure(combined):
            exc = "BuildError"          # the probe never built: the PROBE is broken
        # nothing was reported: the toolchain, the compile or the process died,
        # so the target operation was never reached
        raw.update({"status": "timeout" if res.timed_out else "harness_error",
                    "exception_type": exc, "traceback": combined[-2000:],
                    "failure_stage": "module_loading", "target_reached": False,
                    "records": {}, "iterations": [], "warnings": []})
    else:
        raw.update({k: payload.get(k) for k in
                    ("status", "exception_type", "traceback", "records",
                     "iterations", "warnings", "failure_stage", "target_reached")})
    return raw

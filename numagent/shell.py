from __future__ import annotations

import re
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

from .models import CommandResult

# Cap captured stream size so a runaway build log can't blow up memory / the
# agent's context window later.
_MAX_STREAM = 200_000


def _truncate(s: str) -> str:
    if len(s) <= _MAX_STREAM:
        return s
    head = s[: _MAX_STREAM // 2]
    tail = s[-_MAX_STREAM // 2 :]
    return f"{head}\n... [truncated {len(s) - _MAX_STREAM} chars] ...\n{tail}"


#: Maven/surefire states its counts outright, so it is tried first.
_SUREFIRE = re.compile(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+)"
                       r"(?:,\s*Errors:\s*(\d+))?", re.I)
#: ctest reports the ratio rather than a per-test line.
_CTEST = re.compile(r"(\d+)\s+tests?\s+failed\s+out\s+of\s+(\d+)", re.I)
#: (passed, failed) wordings, in the order runners are tried. pytest, jest and
#: cargo all say "passed/failed"; mocha says "passing/failing".
_COUNT_WORDS = ((r"(\d+)\s+passed", r"(\d+)\s+failed"),
                (r"(\d+)\s+passing", r"(\d+)\s+failing"))
#: cargo prints one summary PER test binary, so its counts are summed rather
#: than read from the first line — and a run whose every binary reports zero is
#: a run that tested nothing, not a run that passed.
_CARGO = re.compile(r"test result:\s*(?:ok|FAILED)\.\s*(\d+)\s+passed;\s*(\d+)\s+failed",
                    re.I)
#: `go test` prints no totals at all — only a line per test.
_GO_FAIL = re.compile(r"^\s*---\s+FAIL:", re.M)
_GO_PASS = re.compile(r"^\s*---\s+PASS:", re.M)


def parse_test_counts(out: str) -> Tuple[Optional[int], Optional[int]]:
    """Return (passed, failed) from a test runner's summary, or (None, None).

    Every ecosystem in this benchmark words it differently, and reading only
    pytest's wording is not a cosmetic gap: a mocha suite that reports "4
    failing" yields no counts, so a genuinely failing revision is graded as an
    *error* — and an instance that reproduces perfectly is thrown away as a setup
    failure. Recognised: pytest, jest, cargo, mocha, maven/surefire, ctest and
    `go test`.
    """
    text = out or ""
    m = _SUREFIRE.search(text)
    if m:
        run, failures, errors = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
        return max(run - failures - errors, 0), failures + errors
    cargo = _CARGO.findall(text)
    if cargo:
        return (sum(int(p) for p, _ in cargo), sum(int(f) for _, f in cargo))
    for passed_pat, failed_pat in _COUNT_WORDS:
        p, f = re.search(passed_pat, text), re.search(failed_pat, text)
        if p or f:
            return (int(p.group(1)) if p else None, int(f.group(1)) if f else None)
    m = _CTEST.search(text)
    if m:
        failed, total = int(m.group(1)), int(m.group(2))
        return max(total - failed, 0), failed
    go_fail, go_pass = len(_GO_FAIL.findall(text)), len(_GO_PASS.findall(text))
    if go_fail or go_pass:
        return go_pass, go_fail
    return None, None


def parse_pytest_counts(out: str) -> Tuple[Optional[int], Optional[int]]:
    """Backwards-compatible alias for :func:`parse_test_counts`."""
    return parse_test_counts(out)


def _run_streaming(command, *, shell: bool, cwd_str, env, timeout_s: float,
                   on_line) -> Tuple[int, str, str, bool]:
    """Run `command`, handing every line to `on_line` as it arrives.

    Capture is unchanged — the caller still gets the whole stdout/stderr — but a
    long build stops being a silent wait. stdout and stderr are pumped by their
    own threads and kept apart: the deterministic parsers read them separately,
    and a probe's marker payload must not end up interleaved into stderr.
    """
    proc = subprocess.Popen(command, shell=shell, cwd=cwd_str, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    chunks = {"stdout": [], "stderr": []}

    def pump(name: str, stream):
        for line in iter(stream.readline, ""):
            chunks[name].append(line)
            try:
                on_line(name, line.rstrip("\n"))
            except Exception:          # noqa: BLE001 — a broken sink never kills a run
                pass
        stream.close()

    pumps = [threading.Thread(target=pump, args=(n, st), daemon=True)
             for n, st in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for t in pumps:
        t.start()

    timed_out = False
    try:
        exit_code = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out, exit_code = True, 124
        proc.kill()
        proc.wait()
    for t in pumps:
        t.join(timeout=5)
    stderr = "".join(chunks["stderr"])
    if timed_out:
        stderr += f"\n[numagent] timed out after {timeout_s}s"
    return exit_code, "".join(chunks["stdout"]), stderr, timed_out


def run(
    command: Union[str, List[str]],
    cwd: Optional[Union[str, Path]] = None,
    timeout_s: float = 900.0,
    env: Optional[dict] = None,
    on_line=None,
) -> CommandResult:
    """Run a shell command, capturing stdout/stderr. Never raises on non-zero
    exit — the caller inspects the structured result instead.

    Pass `on_line(stream, line)` to also receive each line as it is produced.
    """
    shell = isinstance(command, str)
    printable = command if shell else " ".join(command)
    cwd_str = str(cwd) if cwd else None

    start = time.monotonic()
    timed_out = False
    if on_line is not None:
        exit_code, stdout, stderr, timed_out = _run_streaming(
            command, shell=shell, cwd_str=cwd_str, env=env, timeout_s=timeout_s,
            on_line=on_line)
        return CommandResult(command=printable, exit_code=exit_code,
                             stdout=_truncate(stdout), stderr=_truncate(stderr),
                             duration_s=round(time.monotonic() - start, 3),
                             timed_out=timed_out, cwd=cwd_str)
    try:
        proc = subprocess.run(
            command,
            shell=shell,
            cwd=cwd_str,
            env=env,
            capture_output=True,
            # A test that prints raw bytes (numpy's string dtypes do) must not
            # take the whole run down with a UnicodeDecodeError: every stream is
            # decoded forgivingly, the bad bytes shown as U+FFFD.
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        timed_out = True
        exit_code = 124  # conventional timeout exit code
        # TimeoutExpired carries BYTES even under text=True, so decode before
        # appending: a hanging probe (scikit-image 8193's NaN unwrap) otherwise
        # raised "can't concat str to bytes" and the step never ran
        stdout, stderr = e.stdout or "", e.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        stderr += f"\n[numagent] timed out after {timeout_s}s"
    except FileNotFoundError as e:
        exit_code = 127
        stdout = ""
        stderr = f"[numagent] command not found: {e}"

    return CommandResult(
        command=printable,
        exit_code=exit_code,
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        duration_s=round(time.monotonic() - start, 3),
        timed_out=timed_out,
        cwd=cwd_str,
    )


def which(tool: str) -> bool:
    """True if `tool` is on PATH."""
    import shutil

    return shutil.which(tool) is not None

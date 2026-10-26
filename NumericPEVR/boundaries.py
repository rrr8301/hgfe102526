from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

HBEC, BRTG = "HBEC", "BRTG"

_WRITES_TEST = re.compile(
    r"(?:>>?\s*|tee\s+|cp\s+\S+\s+|mv\s+\S+\s+|patch\s+(?:-\S+\s+)*|"
    r"sed\s+-i[^|;]*|python\s+-c\s+['\"].*open\([^)]*['\"]w)"
    r"[^\s;|&]*test[^\s;|&]*\.(?:py|pyx|c|cpp|jl|m|R)", re.I)
_PYTEST_EDIT = re.compile(r"\b(?:--tolerance|--rtol|--atol)\b", re.I)
_DEV_TEST_HINT = re.compile(r"\b(?:tests?/|_test\.py|test_[\w]+\.py)\b", re.I)

_CHANGES_ENV = re.compile(
    r"\b(?:pip\s+(?:install|uninstall)|conda\s+(?:install|remove)|"
    r"apt(?:-get)?\s+(?:install|remove)|python\s+-m\s+pip\s+install|"
    r"meson\s+(?:setup|install)|cmake\b|make\s+install|"
    r"python\s+setup\.py\s+(?:install|develop|build_ext))\b", re.I)

_PIP_INSTALL = re.compile(r"\b(?:python\s+-m\s+)?pip\s+install\b([^;|&]*)", re.I)
_REQ_NAME = re.compile(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?(?:[=<>!~@].*)?$")
_PIP_FLAG_TAKES_VALUE = {
    "-i", "--index-url", "--extra-index-url", "-f", "--find-links", "-r",
    "--requirement", "-c", "--constraint", "-t", "--target", "--prefix",
    "--root", "--python", "--python-version", "--only-binary", "--no-binary",
    "--platform", "--implementation", "--abi", "--upgrade-strategy",
    "--cache-dir", "--log", "--proxy", "--timeout", "--retries",
    "--trusted-host", "--report", "--src", "-d", "--dest", "-e", "--editable",
}


def _requirement_names(args: str) -> list:
    try:
        tokens = shlex.split(args)
    except ValueError:
        tokens = args.split()
    out, skip = [], False
    for raw in tokens:
        if skip:
            skip = False
            continue
        if raw in _PIP_FLAG_TAKES_VALUE:
            skip = True
            continue
        if raw.startswith("-"):
            continue
        if raw in (".", "..") or "/" in raw or raw.endswith((".whl", ".tar.gz", ".zip")):
            continue
        m = _REQ_NAME.match(raw)
        if m:
            out.append(m.group(1))
    return out

_READS_DEV_TEST = re.compile(
    r"\b(?:cat|less|head|tail|grep|rg|find|git\s+show|git\s+diff|git\s+log\s+-p)\b"
    r"[^;|&]*\b(?:test_[\w]*\.py|tests?/)", re.I)


@dataclass(frozen=True)
class Boundary:
    task: str
    search_variable: str
    fixed: str
    allowed: tuple
    forbidden: tuple

    def describe(self) -> str:
        return (f"TASK {self.task} — you may change: {self.search_variable}. "
                f"FIXED, do not touch: {self.fixed}. "
                f"Allowed: {', '.join(self.allowed)}. "
                f"Forbidden: {', '.join(self.forbidden)}.")


BOUNDARIES = {
    HBEC: Boundary(
        task=HBEC,
        search_variable="the environment (E_B, E_F)",
        fixed="the developer test T_dev",
        allowed=("dependency versions", "build configuration",
                 "installation commands", "runtime environment",
                 "system packages"),
        forbidden=("editing the developer test", "relaxing its tolerances",
                   "changing its inputs or assertions", "deselecting it")),
    BRTG: Boundary(
        task=BRTG,
        search_variable="the generated test T_gen",
        fixed="the reconstructed environments E_B, E_F",
        allowed=("input construction", "API invocation", "numerical assertions",
                 "the probe's own code"),
        forbidden=("installing or removing packages", "rebuilding a revision",
                   "reading the developer's test files")),
}


@dataclass
class Check:
    allowed: bool
    reason: str = ""

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason}


_PIP_UNINSTALL = re.compile(
    r"\b(?:python\s+-m\s+)?pip\s+uninstall\b([^;|&]*)", re.I)


def _uninstalls_itself(command: str, package: str) -> str:
    if not package:
        return ""
    want = package.strip().lower().replace("_", "-")
    for m in _PIP_UNINSTALL.finditer(command or ""):
        for name in _requirement_names(m.group(1)):
            if name.lower().replace("_", "-") == want:
                return name
    return ""


def _installs_itself(command: str, package: str) -> str:
    if not package:
        return ""
    want = package.strip().lower().replace("_", "-")
    for m in _PIP_INSTALL.finditer(command or ""):
        for name in _requirement_names(m.group(1)):
            if name.lower().replace("_", "-") == want:
                return name
    return ""


def _runs_dev_test(command: str, dev_tests: tuple) -> str:
    cmd = command or ""
    if not any(r in cmd for r in ("pytest", "unittest", "nose", "go test",
                                  "cargo test", "npm test")):
        return ""
    for item in dev_tests:
        if item and item in cmd:
            return item
    return ""


def check_command(task: str, command: str, *, purpose: str = "",
                  package: str = "", dev_tests: tuple = ()) -> Check:
    cmd = command or ""
    itself = _installs_itself(cmd, package)
    if itself:
        return Check(False, f"installing a released {itself} would replace the "
                            f"checked-out revision with a PyPI build, so both "
                            f"revisions would import the same code and the "
                            f"transition would measure nothing. Build the "
                            f"revision from its own source instead.")
    removed = _uninstalls_itself(cmd, package)
    if removed:
        return Check(False, f"uninstalling {removed} would delete this "
                            f"revision's own build — the thing being measured. "
                            f"Adjust versions of its DEPENDENCIES if the "
                            f"environment is wrong; never remove the subject.")
    if task == HBEC:
        if _WRITES_TEST.search(cmd) and _DEV_TEST_HINT.search(cmd):
            return Check(False, "HBEC may not modify the developer test: the test is "
                                "the fixed oracle, and a reproduction obtained by "
                                "editing it measures the edit, not the bug")
        if _PYTEST_EDIT.search(cmd):
            return Check(False, "HBEC may not alter the test's tolerances")
        return Check(True)
    if task == BRTG:
        ran = _runs_dev_test(cmd, dev_tests)
        if ran:
            return Check(False, f"BRTG may not run the developer's test "
                                f"({ran}): it is the ground truth this task "
                                f"predicts, and its result would become the "
                                f"record the verdict is computed from. Write and "
                                f"run your own test.")
        if _CHANGES_ENV.search(cmd):
            return Check(False, "BRTG runs in frozen environments: installing, "
                                "removing or rebuilding changes the variable this "
                                "task holds fixed")
        if _READS_DEV_TEST.search(cmd):
            return Check(False, "BRTG may not read the developer's test — it is the "
                                "ground truth this task is predicting")
        return Check(True)
    return Check(True)


def check_artifact(task: str, path: str) -> Check:
    p = (path or "").lower()
    if task == HBEC and _DEV_TEST_HINT.search(p):
        return Check(False, "HBEC may not write to a test file")
    return Check(True)


def permitted_actions(task: str) -> tuple:
    return BOUNDARIES[task].allowed


def describe(task: str) -> str:
    return BOUNDARIES.get(task, BOUNDARIES[BRTG]).describe()


_EXACT_FLOAT = re.compile(
    r"==\s*-?(?:\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+)")

_PLACEHOLDER = re.compile(
    r"__[A-Z][A-Z0-9_]{2,}__|\bTODO\b|\bFIXME\b|<FILL[^>]*>|\bPLACEHOLDER\b")

_EXACT_ZERO = re.compile(r"==\s*0(?:\.0*)?\s*(?:[,)]|$)", re.M)


def check_candidate(contents: str) -> Check:
    text = contents or ""
    hit = _PLACEHOLDER.search(text)
    if hit:
        return Check(False, f"the candidate still contains the placeholder "
                            f"{hit.group(0)!r} — fill in the actual value before "
                            f"it is run; a test with an undefined name cannot "
                            f"execute on either revision")
    hit = None
    for line in text.splitlines():
        if "assert" not in line or line.lstrip().startswith("#"):
            continue
        hit = _EXACT_FLOAT.search(line) or _EXACT_ZERO.search(line)
        if hit:
            break
    if hit:
        return Check(False, f"the candidate asserts exact equality against a "
                            f"float ({hit.group(0).strip()!r}). Floating-point "
                            f"results do not reproduce bit-for-bit, so this "
                            f"fails on the FIXED revision too and proves "
                            f"nothing. Use `numpy.testing.assert_allclose(got, "
                            f"expected, rtol=...)` or `pytest.approx`, with a "
                            f"tolerance smaller than the gap between the two "
                            f"revisions' values")
    return Check(True)

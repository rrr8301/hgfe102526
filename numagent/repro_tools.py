from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import probe_contracts, repro_compare
from . import repro_probe_langs as _langs
from .repro_container import Container, container_numerical_probe, detect_probe_language
from .repro_package import PackageOptions, package_reproduction
from .repro_specs import ReproSpec, python_package, sanitizer_reports, test_prelude
from .repro_workspace import BUGGY_DIR, FIXED_DIR
from .tooling import ToolRegistry

_OUT_LIMIT = 6000       # chars of command output returned to the model
_STORE_LIMIT = 60_000   # chars of output retained for deterministic parsing

PURPOSES = ["setup", "build", "test", "inspect", "debug"]

registry = ToolRegistry()


def _empty_store() -> dict:
    return {"commands": [], "last": None, "test": None, "probes": {}}


@dataclass
class RunContext:
    spec: ReproSpec
    container: Container
    # per-revision observation store: results[rev] = {"commands":[…], "last":…,
    # "test":…, "probes":{label: observation}}
    results: dict = field(default_factory=lambda: {"buggy": _empty_store(),
                                                   "fixed": _empty_store()})
    history: list = field(default_factory=list)     # every tool observation, in order
    comparison: Optional[dict] = None
    report: Optional[dict] = None
    workspace_info: dict = field(default_factory=dict)
    default_timeout_s: float = 1800.0               # used when the model omits `timeout`
    iterations: int = 0                             # reasoning steps taken, kept by the strategy
    on_event: Optional[Callable] = None
    detected_languages: dict = field(default_factory=dict)   # working_dir -> language
    module_styles: dict = field(default_factory=dict)        # working_dir -> esm|commonjs
    packages: dict = field(default_factory=dict)             # working_dir -> (name, dir)
    # probe_scripts[revision][label] = the probe's source + how it was run, kept
    # so a finished reproduction can be packaged as a replayable benchmark image
    probe_scripts: dict = field(default_factory=lambda: {"buggy": {}, "fixed": {}})
    package: Optional[PackageOptions] = None    # packaging policy for this run
    packaging: dict = field(default_factory=dict)   # what package_benchmark_image did
    #: the run's LLM client, so a tool can GENERATE code rather than be handed it
    #: (numagent.probe_codegen). Left None by a caller that drives the tools
    #: directly; every generation path then falls back to its template.
    llm: Optional[object] = None
    #: False runs the TEST ORACLE ONLY: no numerical probe is generated or run,
    #: and the attempt stops once the reproducing test has been seen to FAIL on
    #: buggy and PASS on fixed. The arm that measures what FAIL->PASS alone
    #: accepts, and the cheapest way to run the benchmark.
    probes: bool = True
    #: the generated examples probe, kept so the SAME source runs on both
    #: revisions — two revisions measured by two different probes are not a
    #: comparison, and the second call must not pay to regenerate one
    example_probe: dict = field(default_factory=dict)
    #: appended to a refused reinstall: how THIS strategy changes a revision's
    #: environment the sanctioned way, when its task allows that at all (HBEC)
    rebuild_hint: str = ""

    def store(self, revision: str) -> dict:
        return self.results.setdefault(revision, _empty_store())

    def scripts(self, revision: str) -> dict:
        return self.probe_scripts.setdefault(revision, {})

    def package_of(self, working_dir: str) -> tuple:
        """The package a probe must be built in, as ``(name, directory)``.

        Only meaningful where the repository root is not itself a package — a
        Cargo workspace, most notably. Resolved by walking up from the file the
        fix touches to the nearest manifest that declares a package name.
        """
        if working_dir in self.packages:
            return self.packages[working_dir]
        found = ("", "")
        if self.probe_language(working_dir) == "rust":
            for source in (self.spec.source_files or ())[:3]:
                directory = source
                for _ in range(6):
                    directory = directory.rsplit("/", 1)[0] if "/" in directory else ""
                    if not directory:
                        break
                    script = (r"""sed -n 's/^name[[:space:]]*=[[:space:]]*"\(.*\)"/\1/p' """
                              + f"{working_dir}/{directory}/Cargo.toml 2>/dev/null | head -1")
                    name = (self.container.run(script, workdir="/", timeout_s=60).stdout or "").strip()
                    if name:
                        found = (name, directory)
                        break
                if found[0]:
                    break
        self.packages[working_dir] = found
        return found

    def module_style(self, working_dir: str) -> str:
        """'esm' | 'commonjs' | '' — read from the revision's package.json once.

        A model writing a JavaScript probe reaches for `require` about half the
        time, and in an ESM package that costs a whole probe run to discover.
        package.json answers it beforehand.
        """
        if working_dir in self.module_styles:
            return self.module_styles[working_dir]
        style = ""
        if self.probe_language(working_dir) in ("javascript", "typescript"):
            res = self.container.run(_langs.JS_MODULE_STYLE_COMMAND,
                                     workdir=working_dir, timeout_s=60)
            style = _langs.js_module_style(res.stdout)
        self.module_styles[working_dir] = style
        return style

    def probe_language(self, working_dir: str, requested: str = "auto") -> str:
        """The language to probe `working_dir` in: what was asked for, else what
        the revision looks like (detected once per revision), else python."""
        asked = _langs.canonical(requested)
        if asked:
            return asked
        if working_dir not in self.detected_languages:
            # the INSTANCE's language when detection is unsure — not python: a
            # python probe in a gcc image cannot run at all ("no toolchain"),
            # and the revision is C++ either way
            self.detected_languages[working_dir] = (
                detect_probe_language(self.container, working_dir)
                or getattr(self.spec, "language", "") or "python")
        return self.detected_languages[working_dir]

    def emit(self, kind, payload):
        if self.on_event:
            self.on_event(kind, payload)


def _revision_of(working_dir: str) -> str:
    return "fixed" if working_dir.rstrip("/").endswith("fixed") else "buggy"


#: Commands that actually RUN tests. A revision's test outcome is read from the
#: run labelled purpose="test", so an `ls` or a `find` labelled that way becomes
#: a passing test — exit 0, no failures — and two of them make a reproduced bug
#: look like `pass->pass`. The label is the agent's; whether it ran tests is not.
_RUNS_TESTS = re.compile(
    r"\bpytest\b|\bunittest\b|\bnose\b|\btox\b|python -m pytest"
    r"|\bcargo\s+test\b|\bgo\s+test\b|\bctest\b|\bmeson\s+test\b"
    r"|\bmvn\b|\bgradle\b|\bant\b|\bjunit\b"
    r"|\bmocha\b|\bjest\b|\bvitest\b|\bjasmine\b|\bava\b"
    r"|\b(?:npm|yarn|pnpm)\s+(?:run\s+)?test|\bnpx\s+\w*(?:mocha|jest|vitest)"
    # a tape suite is run as a plain program — `node path/to/test/test.main.js`
    r"|\btape\b|\bnode\s+(?:--\S+\s+)*\S*test\S*\.[cm]?js\b|\bnode\s+--test\b"
    r"|\bmake\s+(?:test|check)\b|\bbin/test\b|\brun[_-]?tests?\b|\btest\.sh\b",
    re.I)


def _runs_tests(command: str, output: str = "") -> bool:
    """Did this command run a test suite?

    True when the command names a known runner, or when its output carries a
    runner's own summary — which covers a project-specific wrapper script.
    """
    if _RUNS_TESTS.search(command or ""):
        return True
    passed, failed = repro_compare.parse_test_counts(output or "")
    return passed is not None or failed is not None


def _looks_like_test(ctx: RunContext, command: str) -> bool:
    sel = (ctx.spec.test_selector or "").split("::")[0]
    return "pytest" in command or bool(sel and sel in command)


# --------------------------------------------------------------------------- #
# 1. run_shell_command
# --------------------------------------------------------------------------- #
#: an agent-side (re)install of the package under test, in the revision itself
_REINSTALLS = re.compile(
    r"pip\s+install\s+(?:[^&|;]*\s)?(?:-e\b|--editable\b|\.(?:\s|$))"
    r"|setup\.py\s+(?:build_ext|develop|install)"
    r"|python\s+-m\s+build\b|meson\s+install\b", re.I)


_INSTALLED = re.compile(r"(?:site|dist)-packages")
_IN_PLACE_EDIT = re.compile(r"\bsed\s+-i|\bperl\s+-[a-z]*i|\btee\b|\bpatch\s|open\([^)]*['\"][wa]b?['\"]|write_text\(")
_REDIRECT_TARGET = re.compile(r"(?<![<>&0-9])>>?\s*(['\"]?[^\s'\";&|]+)")
_COPY = re.compile(r"\b(?:cp|mv|ln|rsync|install)\s")
_SHIM = re.compile(r"\b(?:sitecustomize|usercustomize)\.py\b|\S+\.pth\b")


def edits_installed_package(command: str) -> str:
    """The part of `command` that modifies an INSTALLED package, or "".

    The same rule mini-swe-agent's grading applies, so the two systems compare
    like for like: its agent ran llvmlite 0.44.0 with `__version__` rewritten to
    0.45.0 to pass numba's version gate (numba#10088), and copied modules plus a
    `sitecustomize.py` shim into llvmlite (numba#10052). Installing (pip), copying
    a whole real package, and writing the subject's own build files are allowed.
    """
    installed = bool(_INSTALLED.search(command or ""))
    for part in re.split(r"\n|&&|\|\||;", command or ""):
        if re.search(r"\b(?:uv\s+)?pip3?\s+(?:install|uninstall|download)\b", part):
            continue
        into = lambda token: _INSTALLED.search(token) or (installed and token.lstrip("'\"").startswith("$"))  # noqa: E731
        tokens = part.split()
        edit = installed and _IN_PLACE_EDIT.search(part) and (_INSTALLED.search(part) or "$" in part)
        redirect = any(into(t) for t in _REDIRECT_TARGET.findall(part))
        copy = (_COPY.search(part) and len(tokens) > 2 and into(tokens[-1])
                and any(re.search(r"\.(?:py|so|pyd|pth)$", t.strip("'\"")) for t in tokens[1:-1]))
        shim = _SHIM.search(part) and (_IN_PLACE_EDIT.search(part) or _REDIRECT_TARGET.search(part)
                                       or _COPY.search(part))
        if edit or redirect or copy or shim:
            return part.strip()[:200]
    return ""


def _clobbers_prepared_build(ctx: RunContext, revision: str, command: str) -> str:
    """Why this command must not run: it would replace the venv the harness built.

    numba 10088's agent ran `setup.py build_ext --inplace` and `pip install -e .`
    in a revision the harness had ALREADY built. The editable install took over
    from the venv's copy, `import numba` then resolved to the un-built source
    tree ("unknown location"), and `--pyargs numba.tests.…` selected no tests on
    buggy while fixed still worked — read as a stale selector, not as the
    environment having been overwritten.
    """
    build = (ctx.workspace_info or {}).get("build") or {}
    if not (build.get(revision) or {}).get("built"):
        return ""                       # nothing prepared here to protect
    if not _REINSTALLS.search(command or ""):
        return ""
    refusal = (f"refused: {revision} is ALREADY built and installed in "
               f"{revision}/.venv (the harness did it before you started), and this "
               f"command would replace that install with an un-built source tree — "
               f"the package then imports from nowhere and every test and probe "
               f"fails on this revision only. Run tests and probes against the "
               f"existing build. Use `pip install` only for a MISSING third-party "
               f"dependency, never for this package.")
    # numba#10398: the build was right but on the wrong Python, and "nothing
    # needs building" left the agent no route to the change its task allows
    return refusal + (f" {ctx.rebuild_hint}" if ctx.rebuild_hint else " Nothing needs building.")


def current_pins(ctx: RunContext, revision: str) -> dict:
    """The era pins this revision's venv was last built with ({"numpy": "2.4.1", …})."""
    info = ctx.workspace_info or {}
    for source in ((info.get("rebuilds") or {}).get(revision), (info.get("build") or {}).get(revision)):
        if isinstance(source, dict) and source.get("era_pins"):
            return dict(source["era_pins"])
    return {}


def rebuild_revision(ctx: RunContext, revision: str, python_version: str = "",
                     pins=(), recipe: str = "") -> dict:
    """Rebuild one revision's environment through the harness: on another CPython
    and/or with dependency pins narrowed (`["numpy<2.4"]`), and — under build-mode
    agent — with the recipe the agent declared ("compiled" or "pure").

    The record replaces the revision's build entry, so the reinstall guard above
    protects the NEW build exactly as it protected the old one.
    """
    from .repro_workspace import owns_import, rebuild_revision as _rebuild
    rev = "fixed" if revision == "fixed" else "buggy"
    python_version = python_version or ctx.spec.python_version
    what = (f"rebuild {rev} on Python {python_version}" + (f" as {recipe}" if recipe else "")
            + (f" with {', '.join(pins)}" if pins else ""))
    ctx.emit("exec", {"revision": rev, "purpose": "rebuild", "command": what})
    t0 = time.monotonic()
    result = _rebuild(ctx.spec, ctx.container, rev, python_version,
                      era=(ctx.workspace_info or {}).get("era", ""), pins=pins,
                      recipe=recipe)
    build = ctx.workspace_info.setdefault("build", {})
    agent = ctx.workspace_info.get("build_mode") == "agent"
    if agent and isinstance(build, dict):
        # Nothing was built up front, so the agent's build IS the prepared one:
        # recorded where the reinstall guard looks, so it is protected the same way.
        build.pop("skipped", None)
        build[rev] = result
        other = build.get("buggy" if rev == "fixed" else "fixed") or {}
        if (result.get("built") and other.get("built") and result.get("imports_from")
                and result.get("imports_from") == other.get("imports_from")):
            result["built"] = False
            result["error"] = (f"buggy and fixed both import from "
                               f"{result['imports_from']}: the revisions are not isolated")
        ctx.workspace_info["isolation"] = {
            r: {"imports_from": (build.get(r) or {}).get("imports_from", ""),
                "owns_import": owns_import((build.get(r) or {}).get("imports_from", ""), r)}
            for r in ("buggy", "fixed") if isinstance(build.get(r), dict)}
    elif isinstance(build, dict) and "skipped" not in build:
        build[rev] = result
    ctx.workspace_info.setdefault("rebuilds", {})[rev] = result
    if result.get("built"):
        ctx.spec.python_version = python_version     # a later pins-only rebuild keeps it
    record = {"tool": "rebuild_revision", "revision": rev, "python_version": python_version,
              "recipe": recipe, "imports_from": result.get("imports_from", ""),
              "pins": list(pins), "era_pins": result.get("era_pins", {}),
              "built": bool(result.get("built")), "venv_python": result.get("venv_python", ""),
              "error": result.get("error", ""), "runtime_s": round(time.monotonic() - t0, 2)}
    ctx.history.append(record)
    ctx.emit("tool", {"name": "rebuild_revision", **record})
    return result


@registry.tool(parameters={
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "the shell command to run"},
        "working_dir": {"type": "string", "enum": [BUGGY_DIR, FIXED_DIR],
                        "description": "which prepared revision this runs against"},
        "purpose": {"type": "string", "enum": PURPOSES,
                    "description": "what this command is for. Use 'test' for the run of the "
                                   "reproducing test — that is the run compare_execution_results "
                                   "reads for this revision."},
        "timeout": {"type": "integer", "description": "per-command timeout in seconds"},
    },
    "required": ["command", "working_dir"],
})
def run_shell_command(ctx: RunContext, command: str, working_dir: str = BUGGY_DIR,
                      purpose: str = "inspect", timeout: int | None = None) -> dict:
    """Run one setup, build, test, inspection, or debugging command inside the
    prepared Docker workspace. Returns stdout, stderr, exit code, whether it timed
    out, and its runtime. Installed packages persist across calls."""
    rev = _revision_of(working_dir)
    if purpose not in PURPOSES:
        purpose = "inspect"
    edit = edits_installed_package(command)
    refused = (f"refused: this modifies an installed package (`{edit}`). Faking a "
               f"dependency's version, shimming it with sitecustomize/.pth, or editing "
               f"files in site-packages is not a reproduction. Install a real released "
               f"version instead (a rebuild with pins, e.g. [\"llvmlite==0.44.0\"])."
               ) if edit else _clobbers_prepared_build(ctx, rev, command)
    if refused:
        ctx.history.append({"tool": "run_shell_command", "revision": rev,
                            "command": command, "purpose": purpose, "refused": refused})
        ctx.emit("tool", {"name": "run_shell_command", "revision": rev,
                          "purpose": purpose, "command": command, "refused": refused})
        return {"revision": rev, "working_dir": working_dir, "command": command,
                "purpose": purpose, "exit_code": None, "refused": refused}
    if purpose == "inspect" and _looks_like_test(ctx, command):
        purpose = "test"                     # the model forgot to label its test run

    # announced before it runs, so streamed output has something to belong to
    ctx.emit("exec", {"revision": rev, "purpose": purpose, "command": command,
                      "working_dir": working_dir})
    t0 = time.monotonic()
    res = ctx.container.run(command, workdir=working_dir,
                            timeout_s=float(timeout) if timeout else ctx.default_timeout_s)
    elapsed = round(time.monotonic() - t0, 2)

    record = {"tool": "run_shell_command", "revision": rev, "working_dir": working_dir,
              "command": command, "purpose": purpose, "exit_code": res.exit_code,
              "timed_out": res.timed_out, "runtime_s": elapsed,
              "stdout": res.stdout[-_STORE_LIMIT:], "stderr": res.stderr[-_STORE_LIMIT:],
              # Combined stream, what the deterministic test parser reads. Each
              # half is trimmed SEPARATELY: cargo writes its progress and
              # warnings to stderr and its test summaries to stdout, so keeping
              # the tail of `stdout + stderr` on a noisy build discards every
              # "test result:" line and the run reads as having tested nothing.
              "output": (res.stdout[-_STORE_LIMIT // 2:]
                         + res.stderr[-_STORE_LIMIT // 2:])}
    store = ctx.store(rev)
    if purpose == "test" and not _runs_tests(command, record["output"]):
        # labelled a test, but it ran no tests — keep it as an observation and
        # leave the revision's test outcome alone rather than recording `ls`
        # as a pass
        record["purpose"] = purpose = "inspect"
        record["mislabelled_as_test"] = True
    if purpose == "test" and getattr(ctx.spec, "sanitizer", ""):
        # the reports that point into the fix's code, kept ON the record: every
        # reader of the test outcome then sees them without being told to look
        record["sanitizer_reports"] = sanitizer_reports(ctx.spec, rev, record["output"])
    store["commands"].append(record)
    store["last"] = record
    if purpose == "test":
        store["test"] = record

    ctx.history.append({k: record[k] for k in
                        ("tool", "revision", "command", "purpose", "exit_code", "runtime_s")})
    if purpose == "test":
        # The test run is the instance's primary oracle, so its output belongs in
        # the trace whether or not the whole container stream is mirrored
        # (`--show-output`): without it a reader sees "fail->pass" with no way to
        # tell WHICH assertion — or which parametrisation — changed state.
        # Emitted after the reclassification above, so a command that was
        # labelled a test but ran none does not dump output as if it had.
        ctx.emit("test_output", {"revision": rev, "command": command,
                                 "exit_code": res.exit_code,
                                 "timed_out": res.timed_out,
                                 "output": record["output"]})
    ctx.emit("tool", {"name": "run_shell_command", "revision": rev, "purpose": purpose,
                      "command": command, "exit_code": res.exit_code,
                      "stdout": record["stdout"][-_OUT_LIMIT:],
                      "stderr": record["stderr"][-_OUT_LIMIT:]})
    return {"revision": rev, "working_dir": working_dir, "command": command,
            "purpose": purpose, "exit_code": res.exit_code, "timed_out": res.timed_out,
            "runtime_s": elapsed,
            "stdout_tail": record["stdout"][-_OUT_LIMIT:],
            "stderr_tail": record["stderr"][-_OUT_LIMIT:]}


# --------------------------------------------------------------------------- #
# 2. run_numerical_probe
# --------------------------------------------------------------------------- #
_PROBE_CODE_DOC = (
    "Source executed inside the revision, in the revision's own language — it runs "
    "with that revision's directory as the working directory, and the probe is "
    "built inside the revision, so an import resolves to THIS revision's code. "
    "Record an output value with the injected probe API and one solver/loop step "
    "with the iteration API (use a 'residual' or 'error' field so convergence can "
    "be judged); a value recorded as 'residual' is scored against residual_tol. "
    "Anything the probe prints is kept too, and NaN/Inf state plus FP exceptions "
    "(overflow / invalid / divide-by-zero / underflow) are captured automatically. "
    "The code is checked against the language's probe contract BEFORE it runs: a "
    "probe in the wrong language, one that records nothing, or one that defines the "
    "entry point the harness owns is refused without executing, and nothing is "
    "observed for that revision. What `code` must contain, per language — "
    + " | ".join(f"{lang}: {doc}" for lang, doc in _langs.CODE_CONTRACT.items())
)

_LANGUAGE_DOC = (
    "language the probe is written in. Default 'auto' detects it from the revision "
    "(go.mod → go, Cargo.toml → rust, pom.xml/build.gradle → java, tsconfig.json → "
    "typescript, package.json → javascript, pyproject.toml/setup.py → python, "
    "*.c/*.cpp → c/cpp). Set it explicitly to override. The probe must be written in "
    "the revision's language — see `code` for that language's required shape."
)


@registry.tool(parameters={
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": _PROBE_CODE_DOC},
        "working_dir": {"type": "string", "enum": [BUGGY_DIR, FIXED_DIR],
                        "description": "which prepared revision to probe"},
        "language": {"type": "string",
                     "enum": ["auto", *sorted(_langs.CODE_CONTRACT)],
                     "description": _LANGUAGE_DOC},
        "extra_flags": {"type": "string",
                        "description": "compiled languages only: extra compiler flags for c/cpp "
                                       "(-I… -L… -l…), extra classpath entries for java, extra "
                                       "cargo/go arguments for rust/go"},
        "label": {"type": "string",
                  "description": "name for this probe; run the SAME label with the SAME code on "
                                 "both revisions so the two observations pair up"},
        "expected": {"type": "array", "items": {"type": "number"},
                     "description": "optional reference values the recorded values are scored "
                                    "against (absolute/relative error, ULP distance, correct digits)"},
        "rtol": {"type": "number", "description": "relative tolerance for the reference (default 1e-7)"},
        "atol": {"type": "number", "description": "absolute tolerance for the reference (default 0)"},
        "residual_tol": {"type": "number",
                         "description": "tolerance for a recorded residual / iteration error (default 1e-9)"},
        "timeout": {"type": "integer", "description": "timeout in seconds (default 300)"},
    },
    "required": ["code", "working_dir", "label"],
})
def _probes_disabled(ctx: RunContext, tool: str, label: str = "") -> dict:
    """The refusal a probe tool returns under `--no-probes`, recorded like any
    other observation so the trace shows what was skipped and why."""
    note = ("numerical probes are disabled for this run (--no-probes): the test "
            "transition is the only oracle. Finish by reporting the result.")
    ctx.history.append({"tool": tool, "label": label, "refused": note})
    ctx.emit("tool", {"name": tool, "label": label, "refused": note})
    return {"tool": tool, "label": label, "refused": note, "status": "skipped"}


def run_numerical_probe(ctx: RunContext, code: str, working_dir: str, label: str,
                        expected: list | None = None, rtol: float = repro_compare.DEFAULT_RTOL,
                        atol: float = repro_compare.DEFAULT_ATOL,
                        residual_tol: float = repro_compare.DEFAULT_RESIDUAL_TOL,
                        timeout: int = 300, language: str = "auto",
                        extra_flags: str = "") -> dict:
    """Execute issue-specific numerical code in one revision — in that revision's
    language — and capture the structured values validation needs: recorded
    outputs, residuals, NaN/Inf and IEEE FP-exception state, iteration traces, and
    error metrics against a reference. Run the same label on BOTH revisions."""
    if not ctx.probes:
        return _probes_disabled(ctx, "run_numerical_probe", label)
    rev = _revision_of(working_dir)
    lang = ctx.probe_language(working_dir, language)
    style = ctx.module_style(working_dir) if lang in ("javascript", "typescript") else ""
    # LAYER 1 — probe contract. Deterministic, pre-execution: is this code
    # structurally a probe of THIS language? A failure here is an infrastructure
    # error, never evidence about the revision, so nothing is recorded.
    contract = probe_contracts.validate_probe(lang, code, module_style=style)
    if not contract.valid:
        feedback = contract.feedback(lang)
        ctx.history.append({"tool": "run_numerical_probe", "revision": rev, "label": label,
                            "language": lang, "status": "probe_generation_error",
                            "errors": contract.errors})
        ctx.emit("tool", {"name": "run_numerical_probe", "revision": rev, "label": label,
                          "language": lang, "probe_status": "probe_generation_error",
                          "contract_status": "fail", "reasons": contract.errors})
        return {"status": "probe_generation_error", "contract_status": "fail",
                "execution_status": "not_attempted", "target_reached": False,
                "records": {}, "usable_for_numerical_verification": False,
                "errors": contract.errors, "error": feedback,
                # one line for the execution trace; `error` is the full contract
                # text the model needs in order to regenerate the probe
                "error_brief": f"probe contract ({lang}): " + " ".join(contract.errors),
                "revision": rev, "label": label, "language": lang}
    ctx.emit("exec", {"revision": rev, "purpose": "probe", "working_dir": working_dir,
                      "command": f"numerical probe {label!r} ({lang})"})
    raw = container_numerical_probe(ctx.container, code, workdir=working_dir,
                                    timeout_s=float(timeout), language=lang,
                                    extra_flags=extra_flags or "",
                                    package=ctx.package_of(working_dir))
    plan = raw.pop("plan", None)          # replay material, not an observation
    ub_reports = (sanitizer_reports(ctx.spec, rev, (raw.get("stderr") or "") + "\n"
                                    + (raw.get("stdout") or ""))
                  if getattr(ctx.spec, "sanitizer", "") else [])
    obs = repro_compare.analyze_probe(raw, label=label, expected=expected,
                                      rtol=rtol, atol=atol, residual_tol=residual_tol,
                                      package=(python_package(ctx.spec)
                                               if lang == "python" else ""))
    obs["revision"] = rev
    obs["language"] = raw.get("language", lang)
    if ub_reports and obs.get("usable"):
        # undefined behaviour in the fix's code, caught by the sanitizer build:
        # a defect signal on this revision exactly as a NaN would be
        obs["pathology"]["present"] = True
        obs["pathology"]["reasons"] = [f"UBSan: {r}" for r in ub_reports[:3]] \
            + list(obs["pathology"]["reasons"])
        obs["sanitizer_reports"] = ub_reports
    # A probe that never loaded gets the repository's own answer about how to
    # import it, so the next attempt is informed rather than another guess.
    if style and obs.get("target_reached") is False:
        obs["pathology"]["reasons"] = ([_langs.js_import_hint(style)]
                                       + list(obs["pathology"]["reasons"]))
    _annotate_layers(obs)
    # A later run under the same label replaces the earlier one — unless the
    # earlier one MEASURED something and this one did not. A re-run with a bad
    # import must not wipe the evidence a good run already recorded: that is
    # how a clean buggy (2,2,1) vs fixed (2,2,3) covariance became "not
    # observed". The model still sees this failure; only the evidence stays.
    earlier = ctx.store(rev)["probes"].get(label)
    if _measured(earlier) and not _measured(obs):
        obs["kept_earlier_run"] = True
        ctx.emit("thought", f"[{rev}] this run of {label!r} recorded nothing usable "
                            f"({obs.get('status')}); the earlier run's observations are kept")
        ctx.history.append({"tool": "run_numerical_probe", "revision": rev, "label": label,
                            "status": obs.get("status"), "kept_earlier_run": True})
        return obs
    ctx.store(rev)["probes"][label] = obs
    ctx.scripts(rev)[label] = {
        "label": label, "revision": rev, "working_dir": working_dir,
        "language": obs["language"], "code": code, "extra_flags": extra_flags or "",
        "expected": expected, "rtol": rtol, "atol": atol,
        "residual_tol": residual_tol, "timeout": timeout,
        "status": obs["status"], "pathology": obs["pathology"]["present"],
        "plan": plan,
    }

    ctx.history.append({"tool": "run_numerical_probe", "revision": rev, "label": label,
                        "language": obs["language"], "status": obs["status"],
                        "pathology": obs["pathology"]["present"],
                        "reasons": obs["pathology"]["reasons"][:3],
                        "expected": obs.get("expected"),
                        "observed": obs.get("values", [])[:10]})
    ctx.emit("tool", {"name": "run_numerical_probe", "revision": rev, "label": label,
                      "language": obs["language"],
                      "pathology": obs["pathology"]["present"],
                      "expected": obs.get("expected"),
                      "observed": obs.get("observed_records") or obs.get("values"),
                      "accuracy": obs.get("accuracy"),
                      # why an observation came back empty: did the probe run at
                      # all, did it record anything, did the values parse
                      "probe_status": obs.get("status"),
                      "n_records": obs.get("n_records"),
                      "n_values": obs.get("n_values"),
                      "values_source": obs.get("values_source"),
                      "reasons": obs["pathology"]["reasons"],
                      "traceback": obs.get("traceback_tail", ""),
                      "stdout": obs.get("stdout_tail", ""),
                      "stderr": obs.get("stderr_tail", "")})
    if style:
        obs["module_style"] = style
    return obs


def _measured(obs) -> bool:
    """Did this probe run record something usable about the revision?"""
    return bool(obs and obs.get("usable") and obs.get("n_records"))


#: observation status -> what the execution layer did with the probe
_EXECUTION_STATUS = {
    "ok": "completed",
    "exception": "completed_with_exception",
    "probe_error": "failed",
    "environment_error": "failed",
    "timeout": "timeout",
    "harness_error": "failed",
}


def _annotate_layers(obs: dict) -> dict:
    """Stamp the three verification layers onto an observation.

    Layer 1 passed to get here. Layer 2 says whether the probe ran and reached
    the target operation; only then may layer 3 read its numbers as evidence.
    """
    obs["contract_status"] = "pass"
    obs["execution_status"] = _EXECUTION_STATUS.get(obs.get("status"), "failed")
    obs["usable_for_numerical_verification"] = bool(
        obs.get("usable") and obs.get("target_reached") is not False)
    return obs


# --------------------------------------------------------------------------- #
# 3. check_reported_examples
# --------------------------------------------------------------------------- #
EXAMPLES_LABEL = "reported_examples"

_PREAMBLE_DOC = (
    "OPTIONAL hint: the imports and bindings the examples need, in the revision's "
    "language — \"from sympy import *\" plus the symbols they use, or the "
    "require/import of the module under test. On a Python revision the harness "
    "WRITES the probe (it is given the report's examples and what the two "
    "revisions are expected to do); anything you put here is passed to that "
    "generation as a hint, and used directly if generation fails. Elsewhere it is "
    "the preamble of the harness's template probe. The example expressions "
    "themselves always come from the report — never restate them here."
)


@registry.tool(parameters={
    "type": "object",
    "properties": {
        "working_dir": {"type": "string", "enum": [BUGGY_DIR, FIXED_DIR],
                        "description": "which prepared revision to evaluate them in"},
        "preamble": {"type": "string", "description": _PREAMBLE_DOC},
        "language": {"type": "string",
                     "enum": ["auto", *sorted(_langs.EXAMPLE_LANGUAGES)],
                     "description": "language of the revision (default 'auto' detects it)"},
        "timeout": {"type": "integer", "description": "timeout in seconds (default 300)"},
    },
    "required": ["working_dir"],
})
def check_reported_examples(ctx: RunContext, working_dir: str, preamble: str = "",
                            language: str = "auto", timeout: int = 300,
                            code: str = "") -> dict:
    """Evaluate the issue's OWN reported input/output examples in one revision and
    record what each produced. The harness builds the probe from the report and
    names every record after the example's input, so the values line up across
    revisions automatically — you only supply the imports/bindings the expressions
    need. Run it on BOTH revisions: that is what shows whether the reported buggy
    value appears on buggy and the expected value appears on fixed."""
    if not ctx.probes:
        return _probes_disabled(ctx, "check_reported_examples")
    rev = _revision_of(working_dir)
    examples = [(str(e.get("input", "")), str(e.get("expected_output", "") or ""))
                for e in ((ctx.spec.strategy or {}).get("input_output_examples") or [])
                if isinstance(e, dict) and str(e.get("input", "")).strip()]
    if not examples:
        return {"revision": rev, "checked": 0,
                "error": "this issue's strategy mined no input/output examples"}

    lang = ctx.probe_language(working_dir, language)
    # `code` is a probe the caller generated (numagent.probe_codegen) — already
    # validated against the contract and the record-name rule. Running the SAME
    # source on both revisions is what makes the two runs comparable, so the
    # caller generates once and passes the same string here twice.
    generated = {}
    if not code:
        # GENERATED PROBE (python). Written once per run and reused for the second
        # revision: the comparison is only meaningful if both revisions ran the
        # same source.
        code, generated = _generated_example_probe(ctx, lang, preamble)
    if not code:
        # the reproducing test already imports this repository correctly; that is
        # a better default than any guess, and it is what the examples were
        # written in
        preamble = preamble or test_prelude(ctx.spec, lang)
        try:
            code = _langs.example_probe_source(lang, examples, preamble)
        except ValueError as e:
            return {"revision": rev, "checked": 0, "error": str(e)}

    obs = run_numerical_probe(ctx, code, working_dir, EXAMPLES_LABEL,
                              language=lang, timeout=timeout)
    recorded = {k: v for k, v in (obs.get("observed_records") or {}).items()
                if not k.startswith(_langs.EXPECTED_PREFIX)}
    failed = {k: v for k, v in recorded.items()
              if _langs.is_example_error(v)}
    return {"revision": rev, "language": obs.get("language"), "status": obs.get("status"),
            "preamble": preamble, "probe": generated or {"generated": False},
            "checked": len(examples), "recorded": len(recorded),
            "observed": recorded,
            # a preamble that does not bind what the expressions need shows up here,
            # and as "not observed" in the comparison — never as a wrong value
            "could_not_evaluate": failed,
            "traceback_tail": obs.get("traceback_tail", "")}


def _generated_example_probe(ctx: RunContext, language: str, hint: str = "") -> tuple:
    """The run's generated examples probe, written on first use and cached.

    Returns ``(code, meta)``; ``("", meta)`` when generation is unavailable or
    did not produce a probe that validates, which leaves the caller on the
    template path. Generation needs a client on the context — a harness driving
    the tools without one keeps exactly the old behaviour.
    """
    from .probe_codegen import LANGUAGE as CODEGEN_LANGUAGE
    from .probe_codegen import generate_example_probe

    if ctx.example_probe:                       # already written for this run
        meta = dict(ctx.example_probe)
        return meta.pop("code", ""), meta
    if ctx.llm is None or language != CODEGEN_LANGUAGE:
        return "", {"generated": False}
    strategy = ctx.spec.strategy or {}
    examples = [(str(e.get("input", "")), str(e.get("buggy_output", "") or ""),
                 str(e.get("expected_output", "") or ""))
                for e in (strategy.get("input_output_examples") or [])
                if isinstance(e, dict) and str(e.get("input", "")).strip()]
    if not examples:
        return "", {"generated": False}
    hints = "\n".join(h for h in (hint.strip(),
                                  (f"The reproducing test imports it as: "
                                   f"{test_prelude(ctx.spec, language)}"
                                   if test_prelude(ctx.spec, language) else "")) if h)
    result = generate_example_probe(
        ctx.llm, language=language, examples=examples,
        behavior={k: strategy.get(k, "") for k in
                  ("summary", "expected_buggy_behavior", "expected_fixed_behavior",
                   "success_criterion")},
        hints=hints)
    if not result.get("code"):
        ctx.emit("thought", f"the examples probe could not be generated "
                            f"({result.get('error')}); using the template probe")
        return "", {"generated": False, "error": result.get("error")}
    ctx.example_probe = {"code": result["code"], "generated": True,
                         "attempts": result.get("attempts"),
                         "unusable": result.get("unusable") or [],
                         "notes": result.get("notes", "")[:200]}
    ctx.emit("thought", f"EXAMPLES PROBE (generated, attempt "
                        f"{result.get('attempts')}):\n{result['code'][:600]}")
    meta = dict(ctx.example_probe)
    return meta.pop("code", ""), meta


# --------------------------------------------------------------------------- #
# 4. compare_execution_results  (deterministic)
# --------------------------------------------------------------------------- #
@registry.tool(parameters={"type": "object", "properties": {}, "required": []})
def compare_execution_results(ctx: RunContext) -> dict:
    """Deterministically compare the buggy and fixed observations — the reproducing
    test's outcome on each revision, and every numerical probe run on both — against
    the strategy's success criterion and the numerical oracle. No LLM reasoning."""
    ctx.comparison = repro_compare.compare(ctx.results.get("buggy") or {},
                                           ctx.results.get("fixed") or {},
                                           ctx.spec)
    examples = ctx.comparison.get("examples") or {}
    ctx.history.append({"tool": "compare_execution_results",
                        "transition": ctx.comparison["transition"],
                        "fail_to_pass": ctx.comparison["fail_to_pass"],
                        "numeric_verdict": ctx.comparison["numeric"]["verdict"],
                        "examples_verdict": examples.get("verdict"),
                        "examples": f"{examples.get('n_reproduced')}/{examples.get('n_judged')}"})
    ctx.emit("tool", {"name": "compare_execution_results",
                      "transition": ctx.comparison["transition"],
                      "numeric": ctx.comparison["numeric"]["verdict"],
                      "examples": examples.get("verdict")})
    return ctx.comparison


# --------------------------------------------------------------------------- #
# 5. package_benchmark_image  (deterministic)
# --------------------------------------------------------------------------- #
@registry.tool(parameters={
    "type": "object",
    "properties": {
        "notes": {"type": "string",
                  "description": "optional: what this instance demonstrates, kept in the "
                                 "image's metadata. It does NOT influence the status."},
    },
    "required": [],
})
def package_benchmark_image(ctx: RunContext, notes: str = "") -> dict:
    """Package the reproduction you just confirmed as a self-contained benchmark
    image named <repo>-<issue_id>. It captures the container exactly as it stands
    now — both revisions with everything you installed and built — plus the source
    and test patches and every numerical probe you ran, each replayable per
    revision, and a buggy-vs-fixed comparison entry point. Call it once, AFTER
    compare_execution_results shows the defect on buggy and its absence on fixed,
    and BEFORE finalize_reproduction. Nothing you do after this call is captured.
    """
    options = ctx.package
    if options is None or not options.enabled:
        info = {"packaged": False, "image": "",
                "error": "packaging is not enabled for this run"}
    else:
        if ctx.comparison is None:
            compare_execution_results(ctx)
        # the verdict the image records: the real one if the run already
        # finalized, else the same deterministic verdict computed now
        report = ctx.report or repro_compare.finalize(ctx.comparison, ctx.spec, notes=notes)
        info = package_reproduction(ctx, options=options, report=report, notes=notes)
    ctx.packaging = info
    ctx.history.append({"tool": "package_benchmark_image",
                        "packaged": info.get("packaged"), "image": info.get("image"),
                        "error": info.get("error", "")})
    ctx.emit("tool", {"name": "package_benchmark_image", **info})
    return info


# --------------------------------------------------------------------------- #
# 6. finalize_reproduction  (deterministic, terminal)
# --------------------------------------------------------------------------- #
@registry.tool(parameters={
    "type": "object",
    "properties": {
        "notes": {"type": "string",
                  "description": "optional: what you observed, for the record. It is attached to "
                                 "the report but does NOT influence the status."},
    },
    "required": [],
})
def finalize_reproduction(ctx: RunContext, notes: str = "") -> dict:
    """Produce the final reproduction status and its evidence report. Deterministic;
    the status is one of REPRODUCED / PARTIALLY_REPRODUCED / NOT_REPRODUCED /
    SETUP_FAILURE / INVALID_STRATEGY / AMBIGUOUS_RESULT — or F2P_REPRODUCED when
    the run was made with `--no-probes` and the test transition is the whole of
    the evidence. Call this LAST — it ends the task."""
    if ctx.comparison is None:
        compare_execution_results(ctx)
    ctx.report = repro_compare.finalize(ctx.comparison, ctx.spec, notes=notes,
                                        probes=ctx.probes)
    ctx.report["workspace"] = ctx.workspace_info
    ctx.history.append({"tool": "finalize_reproduction", "status": ctx.report["status"],
                        "rationale": ctx.report["rationale"]})
    ctx.emit("tool", {"name": "finalize_reproduction", "status": ctx.report["status"]})
    return ctx.report



# --------------------------------------------------------------------------- #
# what a strategy may offer its model
# --------------------------------------------------------------------------- #
def available_tools(ctx: RunContext) -> list:
    """Tool names this run may use. Packaging is hidden unless the run was
    started with it enabled — an unusable tool in the schema list is a trap the
    model pays attention tokens for and can only fail."""
    packaging_on = bool(ctx.package and ctx.package.enabled)
    return [n for n in registry.names()
            if packaging_on or n != "package_benchmark_image"]

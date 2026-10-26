from __future__ import annotations

import json
import functools
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from . import shell

DEFAULT_PYTHON = "3.11"
DEFAULT_BUILD_COMMANDS = ("pip install .",)
DEFAULT_TEST_DEPS = ("pytest", "hypothesis", "tzdata")

LANGUAGE_IMAGES = {
    "rust": "rust:1-slim",
    "go": "golang:1.22",
    "javascript": "node:22-slim",
    "typescript": "node:22-slim",
    "java": "maven:3-eclipse-temurin-17",
    "c": "gcc:13",
    "cpp": "gcc:13",
    "fortran": "gcc:13",
}

#: Build command hints when the miner supplied none, per language.
LANGUAGE_BUILD_COMMANDS = {
    "python": DEFAULT_BUILD_COMMANDS,
    "rust": ("cargo build",),
    "go": ("go build ./...",),
    "javascript": ("npm install",),
    "typescript": ("npm install",),
    "java": ("mvn -q -DskipTests package",),
    "c": ("make",),
    "cpp": ("make",),
    "fortran": ("make",),
}

#: Mined `environment.language` strings, and source-file extensions, mapped to
#: the canonical language names above.
_LANGUAGE_ALIASES = {
    "python": "python", "py": "python",
    "rust": "rust", "go": "go", "golang": "go",
    "javascript": "javascript", "js": "javascript", "node": "javascript",
    "nodejs": "javascript", "typescript": "typescript", "ts": "typescript",
    "java": "java", "kotlin": "java",
    "c": "c", "c++": "cpp", "cpp": "cpp", "cxx": "cpp",
    "fortran": "fortran", "f90": "fortran",
}
_EXT_LANGUAGE = {
    ".py": "python", ".rs": "rust", ".go": "go", ".js": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".java": "java", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".f": "fortran", ".f90": "fortran", ".f03": "fortran",
}


def language_of(name: str) -> str:
    """Canonical language for a mined ``environment.language`` string."""
    return _LANGUAGE_ALIASES.get((name or "").strip().lower(), "")


#: languages a Python package compiles extensions from
_COMPILED_FOR_PYTHON = ("c", "cpp", "fortran", "cython")


#: what a report says when the bug is C-level undefined behaviour — strong
#: signals only: "signed overflow" alone also describes Python-level int bugs
_UB_SIGNAL = re.compile(r"\bUBSan\b|undefined[- ]behaviou?r|-fsanitize=undefined", re.I)
_COMPILED_SUFFIXES = {".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".pyx", ".pxd",
                      ".f", ".f90"}
#: `numpy/_core/src/multiarray/datetime.c:3035:20: runtime error: signed integer overflow…`
#: not anchored: under `pytest -s` a report lands mid-line, after the test's
#: name or its progress dots
_UBSAN_REPORT = re.compile(r"(?P<path>[^\s:]+):(?P<line>\d+):(?:\d+:)?\s*"
                           r"runtime error: (?P<msg>[^\n]+)")
#: lines around a changed hunk that still count as "the code the fix changed" —
#: a fix that adds a guard ABOVE the overflowing line leaves that line unchanged
SANITIZER_MARGIN = 10


def sanitizer_regions(spec) -> dict:
    """Where the fix changed the code: ``{revision: {file: [(first, last)]}}``.

    The buggy side is the lines the patch replaces, the fixed side the lines it
    writes, each with a margin. numpy carries known undefined behaviour ELSEWHERE
    (it ships UBSan suppression files), so a report only counts as this bug when
    it points into this fix's code.
    """
    path = Path(getattr(spec, "source_patch_path", "") or "")
    regions = {"buggy": {}, "fixed": {}}
    if not path.is_file():
        return regions
    current = None
    header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            current = name[2:] if name.startswith("b/") else name
            continue
        m = header.match(line)
        if m and current and current != "/dev/null":
            old_start, old_len = int(m.group(1)), int(m.group(2) or 1)
            new_start, new_len = int(m.group(3)), int(m.group(4) or 1)
            regions["buggy"].setdefault(current, []).append(
                (old_start - SANITIZER_MARGIN, old_start + old_len + SANITIZER_MARGIN))
            regions["fixed"].setdefault(current, []).append(
                (new_start - SANITIZER_MARGIN, new_start + new_len + SANITIZER_MARGIN))
    return regions


def sanitizer_reports(spec, revision: str, output: str) -> list:
    """The UBSan reports in `output` that point into the code the fix changed."""
    regions = sanitizer_regions(spec).get(revision) or {}
    found = []
    for m in _UBSAN_REPORT.finditer(output or ""):
        where = re.sub(r"^(?:\.\./|\./)+", "", m.group("path"))
        line = int(m.group("line"))
        for file, spans in regions.items():
            if (where.endswith(file) or file.endswith(where)) and any(
                    lo <= line <= hi for lo, hi in spans):
                report = f"{file}:{line}: runtime error: {m.group('msg').strip()[:200]}"
                if report not in found:
                    found.append(report)
                break
    return found[:10]


@functools.lru_cache(maxsize=256)
def declared_python_floor(repo: str, sha: str) -> str:
    """The lowest Python the project itself supports at `sha` ("" if unstated).

    A mined CI matrix lists what the workflows touch, which is not the same
    thing: sympy's names 3.9 while its setup.py says `python_requires='>=3.10'`,
    and on 3.9 its own annotations fail with "unsupported operand type(s) for
    |". What the project declares is the authority.
    """
    import re as _re
    import urllib.request

    for name, pattern in (("pyproject.toml", r"requires-python\s*=\s*['\"]([^'\"]+)"),
                          ("setup.py", r"python_requires\s*=\s*['\"]([^'\"]+)")):
        try:
            with urllib.request.urlopen(
                    f"https://raw.githubusercontent.com/{repo}/{sha}/{name}", timeout=20) as r:
                text = r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
        m = _re.search(pattern, text)
        if not m:
            continue
        floor = _re.search(r">=\s*3\.(\d{1,2})", m.group(1))
        if floor:
            return f"3.{floor.group(1)}"
    return ""


@functools.lru_cache(maxsize=256)
def _root_is_python_package(repo: str, sha: str) -> bool:
    """Is the repository ROOT a Python package at `sha` (pyproject.toml/setup.py)?

    A C++ library can carry Python tests too — opencv's bindings are tested
    under modules/python/test — without being pip-installable from its root.
    Asked of the API because no checkout exists yet when the image is chosen;
    an unanswered request assumes a package, which the question exists for.
    """
    import json
    import os
    import urllib.request

    token = os.environ.get("GITHUB_TOKEN")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents?ref={sha}",
        headers={"User-Agent": "numagent-benchmark",
                 **({"Authorization": f"token {token}"} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            names = {item.get("name") for item in json.load(resp) if isinstance(item, dict)}
    except Exception:  # noqa: BLE001
        return True
    return bool(names & {"pyproject.toml", "setup.py"})


def language_from_files(files) -> str:
    """The language the changed files are written in — whichever extension the
    patch touches most, ignoring the docs and manifests every PR also edits."""
    from collections import Counter
    counts = Counter()
    for f in files or []:
        lang = _EXT_LANGUAGE.get(Path(str(f)).suffix.lower())
        if lang:
            counts[lang] += 1
    return counts.most_common(1)[0][0] if counts else ""


@dataclass
class ReproSpec:
    """Everything needed to reproduce one bug in a container."""
    slug: str
    repo: str
    buggy_commit: str
    fixed_commit: str
    test_selector: str
    test_patch_path: str                 # split reproducing-test patch (required)
    source_patch_path: str = ""          # split fix patch (only for verify_fix)
    #: files the fix touches — used to find the package a probe must be built in
    source_files: tuple = ()
    #: apt packages the mined environment asks for, and whether this instance
    #: has to be compiled before it can be imported at all
    system_packages: tuple = ()
    needs_build: bool = False
    # "undefined": build with UBSan — a C-level undefined-behaviour bug (a signed
    # overflow that wraps "correctly" on x86) is invisible to an ordinary build
    sanitizer: str = ""
    #: the top-level package the checkout installs, discovered from the checkout
    #: itself (repro_workspace.discover_import_name); "" until then
    import_name: str = ""
    #: the lowest Python the project supports — an era cap never goes below it
    python_floor: str = ""
    #: facts about the PREPARED container that the agent could not know from the
    #: repository alone — e.g. members dropped from a Cargo workspace that no
    #: longer resolves. Filled in by preparation, read by every prompt builder.
    environment_notes: tuple = ()
    build_commands: tuple = DEFAULT_BUILD_COMMANDS
    test_deps: tuple = DEFAULT_TEST_DEPS
    python_version: str = DEFAULT_PYTHON
    language: str = "python"             # canonical: python/rust/go/java/…
    bug_type: str = ""
    # full reproduction strategy plan (environment_setup/build_steps/test_command/…)
    strategy: dict = field(default_factory=dict)

    @property
    def image(self) -> str:
        """The base image this instance is reproduced in — its own toolchain."""
        if self.language and self.language != "python":
            return LANGUAGE_IMAGES.get(self.language, f"python:{self.python_version}-slim")
        # `-slim` ships no compiler, so an instance with C/Fortran extensions
        # cannot be built in it — and scipy that cannot be built cannot be
        # imported, which reads as a broken probe rather than a missing toolchain
        suffix = "" if self.needs_build else "-slim"
        return f"python:{self.python_version}{suffix}"


#: Names that appear in a mined dependency list but are NOT pip packages — a
#: BLAS implementation, the interpreter itself, a compiler. Installing them with
#: pip fails and takes the rest of the list down with it.
_NOT_PIP = {"python", "python3", "openblas", "libblas", "liblapack", "blas",
            "lapack", "atlas", "gcc", "g++", "gfortran", "clang", "ccache",
            "pkg-config", "ninja-build", "make", "cmake", "conda", "mkl"}

#: A mined version reads like ">=2.0.0", "3.12 (CI)" or "unpinned in
#: environment.yml". Only a real comparator is carried over — a bare number is
#: what the project HAPPENED to use, not a requirement, and pinning to it turns
#: a solvable install into a resolver failure.
_REQUIREMENT = re.compile(r"^\s*(==|>=|<=|~=|!=|>|<)\s*([0-9][\w.*]*)")

#: What a build actually needs: the toolchain that compiles the package, plus the
#: runners its tests import. Everything else in a mined environment — the docs
#: stack, the linters, the optional accelerators — costs minutes to install and
#: brings its own resolver conflicts.
_BUILD_ESSENTIAL = re.compile(
    r"^(?:cython|meson|meson-python|ninja|pybind11|pythran|numpy|setuptools|"
    r"wheel|build|packaging|scipy-openblas\d*|pooch|array-api-strict|"
    r"pytest[\w-]*|hypothesis)$", re.I)

#: A repository is COMPILED when its own language list says so — "Python with
#: C/C++ extension code", "Python, C, C++, Cython". Not when the environment
#: merely installs a compiler: sympy's CI apt-installs clang for an optional
#: autowrap test and is still pure Python.
_COMPILED_LANGUAGE = re.compile(r"\bC\b|\bC\+\+|Cython|Fortran|extension", re.I)

#: apt packages that build nothing — a LaTeX stack for the docs, mostly.
_APT_NOT_NEEDED = re.compile(r"texlive|latexmk|doxygen|graphviz|sphinx", re.I)

#: Documentation, linting and benchmarking tools — the project's environment,
#: not this instance's. Installing sphinx to reproduce a numerical bug costs
#: minutes and can fail on its own resolver conflicts.
_NOT_NEEDED = re.compile(r"sphinx|myst|jupyter|pydata|doctest|ruff|lint|"
                         r"^asv$|pyrefly|mypy|^black$|pre-commit|towncrier|"
                         r"coverage|codecov"
                         # pytest plugins that need a GUI toolkit. A project's CI
                         # installs them WITH a Qt binding; installed alone,
                         # pytest-qt aborts plugin loading ("requires either
                         # PySide6, PyQt5 or PyQt6") and pytest exits 4 before a
                         # single test runs — on both revisions, for every pandas
                         # instance.
                         r"|pytest-qt|pytest-xvfb|pytest-xdist-qt", re.I)


def pip_requirements(env: dict, *, essential_only: bool = False) -> tuple:
    """Build- and test-time pip specs from a mined ``dependency_versions`` list."""
    out = []
    for entry in (env or {}).get("dependency_versions") or ():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if (not name or name.lower() in _NOT_PIP or _NOT_NEEDED.search(name)
                or not re.fullmatch(r"[\w.-]+", name)):
            continue
        if essential_only and not _BUILD_ESSENTIAL.match(name):
            continue
        m = _REQUIREMENT.match(str(entry.get("version") or ""))
        out.append(f"{name}{m.group(1)}{m.group(2)}" if m else name)
    return tuple(dict.fromkeys(out))


def system_packages(env: dict, limit: int = 12) -> tuple:
    """apt package names the mined environment asks for, docs stack excluded."""
    names = [str(p).strip() for p in (env or {}).get("system_packages") or ()]
    keep = [n for n in names
            if re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", n) and not _APT_NOT_NEEDED.search(n)]
    return tuple(dict.fromkeys(keep))[:limit]


#: A generated plan says a project is compiled by naming the toolchain that
#: compiles it, not by declaring a language list — there is no mined `language`
#: field to read. These are the markers that only appear when something is
#: actually built: a compiler, a build system, `build_ext`, or pip being told
#: the sources are already prepared. Deliberately NOT here: "pip install -e .",
#: which is how a pure-Python project installs too.
_PLAN_COMPILED = re.compile(
    r"\bcython\b|\bmeson\b|\bcmake\b|\bninja\b|\bgfortran\b|\bg\+\+\b|\bgcc\b|"
    r"build-essential|build_ext|--no-build-isolation|compiled extensions?|"
    r"C extensions?|native extensions?|from source with a compiler", re.I)

#: apt packages a plan may ask for, as a whitelist. A plan is prose: it says
#: "install a C compiler and BLAS", and a free-text apt list read out of prose
#: is how you end up running `apt-get install a` in a container.
_PLAN_APT = {
    "build-essential": r"build-essential|\bgcc\b|\bg\+\+\b|c compiler|c\+\+ compiler",
    "gfortran": r"\bgfortran\b|fortran compiler",
    "cmake": r"\bcmake\b",
    "ninja-build": r"\bninja\b",
    "pkg-config": r"pkg-config",
    "libopenblas-dev": r"openblas|\bblas\b|\blapack\b",
    "swig": r"\bswig\b",
}


def plan_text(plan: dict) -> str:
    """The parts of a generated strategy that describe HOW the project is built."""
    parts = []
    for key in ("environment_setup", "build_steps", "reproduction_steps"):
        value = (plan or {}).get(key) or ()
        parts += [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]
    return "\n".join(parts)


def plan_environment(plan: dict) -> dict:
    text = plan_text(plan)
    compiled = bool(_PLAN_COMPILED.search(text))
    apt = [name for name, marker in _PLAN_APT.items() if re.search(marker, text, re.I)]
    # Anything compiled needs a C compiler, whether or not the plan thought to
    # say so: "meson + ninja" and "builds the Cython extensions" both describe a
    # build that cannot start without cc.
    if compiled and "build-essential" not in apt:
        apt.insert(0, "build-essential")
    return {"needs_build": compiled, "system_packages": tuple(apt)}


def needs_build(env: dict) -> bool:
    """Does this instance have to be COMPILED before it can be imported?"""
    return bool(_COMPILED_LANGUAGE.search(str((env or {}).get("language") or "")))


def python_package(spec) -> str:
    """The import name of a Python instance's package under test (`astropy`).

    Discovered from the checkout once it exists (`spec.import_name`, see
    repro_workspace.discover_import_name), so a repository whose import name
    differs from its own (scikit-learn is `sklearn`) needs no table entry.
    Before that, the repository name stands in — callers treat a name nothing
    imports by as "unknown", never as "missing".
    """
    found = str(getattr(spec, "import_name", "") or "")
    if found:
        return found
    name = str(getattr(spec, "repo", "") or "").rsplit("/", 1)[-1]
    return name.replace("-", "_")


_UNSPECIFIED = re.compile(r"\b(?:unspecified|unknown|not specified|not stated|n/a)\b", re.I)


def _parse_pyversion(runtime: str) -> str:
    """Map a mined ``runtime_version`` spec (e.g. ``>=3.10``) to a concrete tag.

    The answer is prose as often as a spec. "unspecified; … (one branch
    references Python 3.14)" names no version, and taking the first `x.y` in it
    put a 2025 jax commit on a Python its jaxlib had no wheel for. A hedged
    answer yields the default; a range or a CI matrix yields its LOWEST
    version — the project's floor at that commit, and the one its era's
    dependencies are likeliest to have wheels for.
    """
    text = runtime or ""
    if _UNSPECIFIED.search(text):
        return DEFAULT_PYTHON
    minors = [int(m) for m in re.findall(r"\b3\.(\d{1,2})\b", text)]
    return f"3.{min(minors)}" if minors else DEFAULT_PYTHON


#: "Python 3.11 execution", "on py 3.11 only", "CPython 3.12": a trigger that
#: names the interpreter, as opposed to a project's supported range.
_TRIGGER_PYTHON = re.compile(r"\b(?:c?python|py)\s*-?\s*3\.(\d{1,2})\b", re.I)


def trigger_python(conditions) -> str:
    """The Python version a bug's trigger conditions require, or "".

    `_parse_pyversion` takes the project's FLOOR, which is right for building
    but wrong for a version-specific bug: numba#10398 reproduces only on 3.11,
    the floor was 3.10, and the test passed on both revisions. Only an
    unambiguous trigger counts; conditions naming several versions decide nothing.
    """
    # every 3.x in a condition that names Python: "Python 3.10 and 3.12" is two
    minors = {m for c in conditions or () if _TRIGGER_PYTHON.search(str(c))
              for m in re.findall(r"\b3\.(\d{1,2})\b", str(c))}
    return f"3.{minors.pop()}" if len(minors) == 1 else ""


#: CPython minor -> release date. An image newer than the commit is
#: anachronistic: the commit's dependencies had published no wheels for it.
PYTHON_RELEASES = {"3.8": "2019-10-14", "3.9": "2020-10-05", "3.10": "2021-10-04",
                   "3.11": "2022-10-24", "3.12": "2023-10-02", "3.13": "2024-10-07",
                   "3.14": "2025-10-07"}
#: wheels for a new Python arrive over the months after its release
WHEEL_LAG_DAYS = 120


def python_for_era(version: str, era_iso: str) -> str:
    """`version`, or the newest Python that had wheels by `era_iso` if it is newer."""
    import datetime as _dt

    if not era_iso or not version:
        return version
    try:
        era = _dt.date.fromisoformat(era_iso[:10])
    except ValueError:
        return version
    minor = lambda v: int(v.split(".")[1])  # noqa: E731
    usable = [v for v, d in PYTHON_RELEASES.items()
              if _dt.date.fromisoformat(d) + _dt.timedelta(days=WHEEL_LAG_DAYS) <= era]
    if not usable:
        return min(PYTHON_RELEASES, key=minor)
    newest = max(usable, key=minor)
    return version if minor(version) <= minor(newest) else newest


def specs_from_index(index_path, patches_dir, *, strategies_path=None,
                     candidates_path=None, only=None,
                     numerical_only: bool = True) -> list[ReproSpec]:
    """Assemble :class:`ReproSpec` list from the split-patches ``index.json``.

    Test selector + bug type + strategy are joined from the strategies file; build
    commands and python version from the mined candidates (falling back to
    defaults). ``only`` restricts to a set/list of PR numbers.
    """
    patches_dir = Path(patches_dir)
    index = json.loads(Path(index_path).read_text(encoding="utf-8"))
    strat = {}
    if strategies_path and Path(strategies_path).exists():
        raw = json.loads(Path(strategies_path).read_text(encoding="utf-8"))
        # Three shapes exist on disk: the current wrapper carrying the run's
        # usage totals beside the strategies, and — from before it — a bare list,
        # or a bare record for a single-PR run. All three still read.
        if isinstance(raw, dict):
            rows = raw.get("strategies") or ([raw] if raw.get("pr_number") else [])
        else:
            rows = raw
        strat = {s["pr_number"]: s for s in rows if isinstance(s, dict) and s.get("pr_number")}
    cands, only_repo = {}, ""
    if candidates_path and Path(candidates_path).exists():
        raw = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
        rows = raw.get("candidates", raw) if isinstance(raw, dict) else raw
        cands = {c["pr_number"]: c for c in rows}
        # A PR number identifies a pull request only WITHIN its repository, and
        # the index holds every repository at once: scipy#20229 and
        # astropy#20229 both exist. The candidates file names the repository the
        # caller means, so `--pr 20229` resolves to one instance, not two.
        first = next(iter(rows), None)
        only_repo = (raw.get("repo") if isinstance(raw, dict) else "") or \
                    ((first or {}).get("repo") or "")

    only = set(only) if only is not None else None
    specs: list[ReproSpec] = []
    for e in index:
        pr = e.get("pr") or e.get("pr_number")
        if only is not None and pr not in only:
            continue
        if only_repo and e.get("repo") and e["repo"] != only_repo:
            continue
        if numerical_only and not e.get("is_numerical_bug", True):
            continue
        s = strat.get(pr, {})
        plan = s.get("strategy", {}) if isinstance(s, dict) else {}
        selector = plan.get("test_selector") or plan.get("test_command") or ""
        # language, build commands and python version from mined candidate
        # evidence, else from the files the patch touches, else the defaults
        build_cmds, deps, pyver = DEFAULT_BUILD_COMMANDS, DEFAULT_TEST_DEPS, DEFAULT_PYTHON
        language = ""
        apt, compiled = (), False
        c = cands.get(pr)
        if c:
            ans = lambda q: (c["answers"].get(q, {}).get("answer", {}) or {})  # noqa: E731
            # The miner's Q4 carries build+environment together; records mined
            # before the questions were consolidated split them across
            # `commands` and `environment`, and both still exist on disk.
            env = ans("build_repro") or ans("environment")
            bc = (ans("build_repro").get("build_commands")
                  or ans("commands").get("build_commands"))
            if bc:
                build_cmds = tuple(bc)
            pyver = _parse_pyversion(env.get("runtime_version", ""))
            language = language_of(env.get("language", ""))
            compiled = needs_build(env)
            # A compiled instance needs its toolchain installed before anything
            # can import it; a pure-Python one is left exactly as it was, so a
            # mined environment full of optional extras cannot break it.
            if compiled:
                apt = system_packages(env)
                deps = tuple(dict.fromkeys(
                    DEFAULT_TEST_DEPS + pip_requirements(env, essential_only=True)))
        elif plan:
            # No mined candidate for this PR — the no-miner arm, where the
            # generated plan is the only environment evidence there is. Read
            # from it exactly what the image depends on; everything else stays
            # at the defaults, as it does for any run without candidates.
            from_plan = plan_environment(plan)
            compiled = from_plan["needs_build"]
            if compiled:
                apt = from_plan["system_packages"]
        language = (language
                    or language_from_files(list(e.get("source_files") or [])
                                           + list(e.get("test_files") or []))
                    or "python")
        # The reproducing TEST decides how an instance runs. numpy's fixes are C
        # and C++, its tests are pytest — and the C image has no Python, so every
        # command there was "not found" (exit 127). A fix in compiled sources
        # under a Python test means the Python package has to be BUILT.
        if (language in _COMPILED_FOR_PYTHON
                and language_from_files(e.get("test_files") or []) == "python"
                and _root_is_python_package(e["repo"], e["buggy_commit"])):
            language, compiled = "python", True
        # A fix for undefined behaviour in compiled code (numpy's datetime.c
        # signed overflow) passes its own test on an ordinary build: on x86 the
        # overflow wraps and the value comes out right. Only a sanitizer build
        # sees the defect, so one is asked for when the fix is in C/C++ AND the
        # report says undefined behaviour.
        sanitizer = ""
        if (language == "python" and compiled
                and any(Path(str(f)).suffix.lower() in _COMPILED_SUFFIXES
                        for f in e.get("source_files") or ())
                and _UB_SIGNAL.search(json.dumps(plan) + json.dumps(
                    ((c or {}).get("answers") or {}), default=str))):
            sanitizer = "undefined"
        if language != "python":
            # the python defaults describe a python instance and nothing else:
            # a pytest/hypothesis install has no meaning in a cargo image, and
            # `pip install .` is not a fallback for it either
            deps = ()
            if not (c and ans("commands").get("build_commands")):
                build_cmds = LANGUAGE_BUILD_COMMANDS.get(language, DEFAULT_BUILD_COMMANDS)
        # the project's own floor beats a mined CI matrix that lists older jobs
        floor = declared_python_floor(e["repo"], e["buggy_commit"]) if language == "python" else ""
        if floor and int(pyver.split(".")[1]) < int(floor.split(".")[1]):
            pyver = floor
        specs.append(ReproSpec(
            slug=e["slug"], repo=e["repo"],
            buggy_commit=e["buggy_commit"], fixed_commit=e.get("fixed_commit", ""),
            test_selector=selector,
            test_patch_path=str(patches_dir / e["test_patch"]),
            source_patch_path=str(patches_dir / e.get("source_patch", "")) if e.get("source_patch") else "",
            source_files=tuple(e.get("source_files") or ()),
            build_commands=build_cmds, test_deps=deps, python_version=pyver,
            language=language, system_packages=apt, needs_build=compiled,
            sanitizer=sanitizer, python_floor=floor,
            bug_type=e.get("bug_type") or s.get("bug_type", ""),
            strategy=plan,
        ))
    return specs


def git_apply(worktree: Path, patch_file: Path, exclude=()):
    """``git apply`` `patch_file` in `worktree`, falling back to a 3-way merge on
    context drift. Returns the apply :class:`~numagent.models.CommandResult`.

    `exclude` drops paths from the patch — the escape hatch for an entry git
    cannot apply at all (a binary file whose blob the diff never carried), so
    the rest of the patch is not lost with it.
    """
    skip = [f"--exclude={path}" for path in exclude]
    chk = shell.run(["git", "apply", "--check", *skip, str(patch_file)], cwd=worktree)
    apply = (["git", "apply"] + skip + ([] if chk.exit_code == 0 else ["--3way"])
             + [str(patch_file)])
    return shell.run(apply, cwd=worktree)


# --------------------------------------------------------------------------- #
# how THIS repository is imported — taken from the reproducing test, not guessed
# --------------------------------------------------------------------------- #
#: import / alias lines in a test patch. These are the two things a probe cannot
#: infer and a model repeatedly gets wrong: the module path the symbol actually
#: lives behind (``sympy.integrals.manualintegrate``, not ``sympy``) and the
#: local aliases the report's examples were written against (``big``, ``frac``).
_PRELUDE_LINE = re.compile(
    r"^(?:import\s|from\s+\S+\s+import\s|use\s+\w+(?:::|\s)"
    # `const x = require(…)`, `const x = await import(…)`, `const x = math.y`
    r"|(?:const|let|var)\s+\w+\s*=\s*(?:require\(|await\s+import\(|\w+\.\w+)"
    # …and the destructuring form, which is how a JS test names the functions it
    # calls: `const { isInteger, bignumber } = math`. Dropping it leaves the
    # examples calling identifiers nothing ever bound.
    r"|(?:const|let|var)\s*\{[^}]{1,200}\}\s*=\s*[\w.]+)")
_JS_SPECIFIER = re.compile(r"""(['"])(\.\.?/[^'"]+)\1""")


def _rebase_specifier(line: str, test_file: str) -> str:
    """Rewrite a relative module path so it resolves from the REPOSITORY ROOT.

    A test sits several directories down and imports ``'../../../../src/x.js'``;
    a probe runs at the root, where that same module is ``'./src/x.js'``. Copying
    the test's line verbatim would look right and fail.
    """
    import posixpath

    base = posixpath.dirname(test_file)

    def fix(m):
        resolved = posixpath.normpath(posixpath.join(base, m.group(2)))
        return f"{m.group(1)}{'./' + resolved if not resolved.startswith('.') else resolved}{m.group(1)}"

    return _JS_SPECIFIER.sub(fix, line) if base else line


def prelude_from_text(text: str, test_file: str = "") -> str:
    """Extract import/alias lines from a test file's SOURCE (not its diff).

    A patch only shows the hunks that changed. When a PR edits an assertion in
    the middle of a file, its imports — sitting at the top — never appear in the
    diff at all, and a prelude derived from the patch comes back empty. The file
    itself always has them.
    """
    lines, seen = [], set()
    for raw in str(text or "").splitlines():
        stripped = raw.strip()
        if not _PRELUDE_LINE.match(stripped) or stripped in seen:
            continue
        seen.add(stripped)
        lines.append(_rebase_specifier(stripped, test_file))
        if len(lines) >= 14:
            break
    return "\n".join(_close_open_paren(line) for line in lines)


def test_files_of(spec) -> list:
    """Repo-relative paths of the files the reproducing-test patch touches."""
    path = Path(getattr(spec, "test_patch_path", "") or "")
    if not path.is_file():
        return []
    return [ln[6:].strip() for ln in
            path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.startswith("+++ b/")]


def patch_paths_of(patch_path: str) -> list:
    """EVERY path a patch touches, including files it carries no content for.

    :func:`test_files_of` reads `+++ b/` lines, which a binary diff written
    without ``--binary`` does not have — it says only "Binary files /dev/null
    and b/model.onnx differ". Those files are exactly the ones that have to be
    restored from the fixed commit, so the header line is read instead.
    """
    path = Path(patch_path or "")
    if not path.is_file():
        return []
    paths, seen = [], set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("diff --git "):
            continue
        match = re.match(r'diff --git (?:"?a/)(.+?)"? (?:"?b/)(.+?)"?$', line)
        target = match.group(2) if match else ""
        if target and target not in seen:
            seen.add(target)
            paths.append(target)
    return paths


def test_prelude(spec, language: str = "") -> str:
    """The imports and aliases the reproducing test uses, ready to run at the root.

    This is the ground truth a probe needs and the only part of probe-writing that
    is repository-specific rather than issue-specific — so it is derived, not
    asked for. Returns "" when the patch shows nothing importable.
    """
    path = Path(getattr(spec, "test_patch_path", "") or "")
    if not path.is_file():
        return ""
    lines, seen, current = [], set(), ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        # a patch touches several files, each with its own depth — the import
        # must be rebased against the file it was written in, not the first one
        if raw.startswith("+++ b/"):
            current = raw[6:].strip()
            continue
        if raw.startswith(("---", "@@", "diff ", "index ", "new file", "deleted")):
            continue
        # context lines matter as much as added ones: the import a probe needs is
        # usually already in the file, and only shows up as context
        if not raw or raw[0] not in "+ ":
            continue
        stripped = raw[1:].strip()
        if not _PRELUDE_LINE.match(stripped) or stripped in seen:
            continue
        seen.add(stripped)
        lines.append(_rebase_specifier(stripped, current))
        if len(lines) >= 14:
            break
    # a parenthesised import spans lines; half of one is a syntax error, so an
    # unfinished line is closed rather than shipped broken
    return "\n".join(_close_open_paren(line) for line in lines)


def _close_open_paren(line: str) -> str:
    depth = line.count("(") - line.count(")")
    if depth <= 0:
        return line
    return line.rstrip().rstrip(",") + ")" * depth


def environment_block(spec) -> list:
    """What preparation changed about the container, as briefing lines.

    An agent that is not told the workspace was pruned will read the absence of
    a crate as its own mistake and spend steps trying to build it.
    """
    notes = [str(n) for n in (getattr(spec, "environment_notes", ()) or ()) if str(n).strip()]
    if not notes:
        return []
    return ["HOW THIS CONTAINER DIFFERS FROM A PLAIN CHECKOUT (both revisions, identically):",
            *(f"  - {n}" for n in notes)]


def test_prelude_block(spec) -> list:
    """:func:`test_prelude` as briefing lines — empty when there is nothing to say.

    Returned as a list so a caller splices it in with ``*``: a prompt should not
    grow a blank line on instances whose test patch shows no imports.
    """
    prelude = test_prelude(spec, getattr(spec, "language", ""))
    if not prelude:
        return []
    return ["HOW THE REPRODUCING TEST IMPORTS THIS REPO (rebased to the revision root — "
            "use these in a probe instead of guessing an import path):",
            *(f"  {line}" for line in prelude.splitlines())]


#: How a test case is named, per framework. Applied to the ADDED lines of the
#: reproducing-test patch, so the names are the ones this PR introduced.
_ADDED_CASE = (
    # pytest collects any `test*` name — unittest/absltest suites (jax, numpy's
    # older tests) are camelCase `testTanhGradSymmetry`, which `test_` missed
    re.compile(r"^\s*def\s+(test\w*)"),                          # pytest / unittest
    re.compile(r"^\s*func\s+(Test\w+)"),                        # go
    re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)"),                   # rust (after #[test])
    re.compile(r"^\s*(?:public\s+)?void\s+(\w+)\s*\("),         # junit
    re.compile(r"""^\s*(?:it|test|describe)(?:\.\w+)?\s*\(\s*['"`]([^'"`]{1,120})"""),  # mocha/jest
    re.compile(r"^\s*TEST(?:_F|_P)?\s*\(\s*(\w+\s*,\s*\w+)"),   # gtest
)


#: `@@ -3234,13 +3234,10 @@ def test_heapsize_P_limit(self):` — git prints the
#: enclosing definition after every hunk header, which is the only place a
#: MODIFIED test announces its own name.
_HUNK_CONTEXT = re.compile(
    r"^@@[^@]*@@\s*(?:async\s+)?(?:def|fn|func|function)\s+([A-Za-z_]\w*)", re.M)
_TEST_NAME = re.compile(r"^(?:test_?|should_|Test)\w*|\w*_test$|\w*Test$")


def _test_patch_sections(spec) -> list:
    """The reproducing-test patch per file: ``[(repo-relative path, [lines])]``.

    A patch that touches several test files names its cases in all of them, and
    a case only runs from the file that defines it — so the file is kept with
    every name read out of the patch.
    """
    path = Path(getattr(spec, "test_patch_path", "") or "")
    if not path.is_file():
        return []
    sections, current = [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("+++ "):
            name = line[4:].strip()
            name = name[2:] if name.startswith("b/") else name
            current = None if name == "/dev/null" else (name, [])
            if current:
                sections.append(current)
        elif current is not None:
            current[1].append(line)
    return sections


#: `it('should return the max of numbers', function () {` — a JavaScript case is
#: a CALL with a string name, not a definition, so no hunk header ever shows it.
_JS_CASE = re.compile(r"""^[ +\-]\s*(?:it|test|specify)(?:\.only|\.skip)?\s*\(\s*"""
                      r"""(['"`])(.+?)\1""")
_JS_TEST_FILE = re.compile(r"\.(?:[cm]?[jt]sx?)$")


def _enclosing_js_cases(lines) -> list:
    """The `it(...)` block each hunk's first change sits in.

    git's hunk header for a JavaScript file names the enclosing SUITE
    (`describe('max', function () {`), never the case, so a patch that adds an
    assertion to an existing test — mathjs#3389 tightening `max` and `min` for
    NaN — shows no case name anywhere a header parser would look. The name is
    still in the hunk, as the last context line opening an `it(` above the
    change. With no such line in view, the whole file is the target ("").
    """
    names, hunk = [], []

    def settle():
        first = next((i for i, line in enumerate(hunk)
                      if line[:1] in "+-" and not line.startswith(("+++", "---"))), None)
        if first is None:
            return
        for line in reversed(hunk[:first + 1]):
            m = _JS_CASE.match(line)
            if m:
                names.append(m.group(2))
                return
        names.append("")

    for line in lines:
        if line.startswith("@@"):
            settle()
            hunk = []
        else:
            hunk.append(line)
    settle()
    return names


#: a definition line inside a hunk (context, added or removed): `    def test_x(self):`
_DEF_LINE = re.compile(r"^(\s*)(?:async\s+)?(?:def|fn|func)\s+([A-Za-z_]\w*)")


def _enclosing_cases(lines: list) -> list:
    """The test cases whose BODIES the patch's changed lines fall in.

    The hunk header names the last definition that starts BEFORE the hunk, not
    the one the change is in: scikit-image#7473's header says
    `test_ellipsoid_levelset`, but the hunk then opens `def test_ellipsoid_stats():`
    and every changed line is inside that one, so the harness ran the wrong test
    (pass->pass) on every iteration. A nested helper (numba's tests define
    `def pyfunc(...)` inside the test) stays part of the test that encloses it.
    """
    found, current, indent = [], None, None
    for line in lines:
        if line.startswith("@@"):
            m = _HUNK_CONTEXT.match(line)
            current, indent = (m.group(1) if m else None), None
            continue
        if line.startswith(("+++", "---")) or line[:1] not in (" ", "+", "-"):
            continue
        m = _DEF_LINE.match(line[1:])
        if m:
            depth, name = len(m.group(1).expandtabs()), m.group(2)
            nested = (current and _TEST_NAME.match(current) and not _TEST_NAME.match(name)
                      and (indent is None or depth > indent))
            if not nested:
                current, indent = name, depth
        if line[:1] in "+-" and line[1:].strip() and current and _TEST_NAME.match(current) \
                and current not in found:
            found.append(current)
    return found


def _modified_case_pairs(spec) -> list:
    pairs = []
    for test_file, lines in _test_patch_sections(spec):
        if _JS_TEST_FILE.search(test_file):
            found = _enclosing_js_cases(lines)
        else:
            found = _enclosing_cases(lines)
        for name in found:
            if (test_file, name) not in pairs:
                pairs.append((test_file, name))
    return pairs


def _added_case_pairs(spec) -> list:
    pairs, seen = [], set()
    for test_file, lines in _test_patch_sections(spec):
        prev_attr = False
        for line in lines:
            if not line.startswith("+") or line.startswith("+++"):
                continue
            body = line[1:]
            # Rust marks the case on the PRECEDING line, so only take `fn` after it
            is_attr = bool(re.match(r"\s*#\[(?:test|tokio::test|rstest)", body))
            for i, pattern in enumerate(_ADDED_CASE):
                m = pattern.search(body)
                if not m:
                    continue
                if i == 2 and not prev_attr:      # a bare `fn` that is not a test
                    continue
                name = m.group(1).strip()
                if name and (test_file, name.lower()) not in seen:
                    seen.add((test_file, name.lower()))
                    pairs.append((test_file, name))
                break
            prev_attr = is_attr
    return pairs


_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def patch_change_points(spec) -> list:
    """``(test file, line)`` of each hunk's first change, in the PATCHED file.

    For a patch that changes code INSIDE a test — a nested `impl`, a parameter
    list — and so names no test itself: the enclosing test is found from here.
    """
    points = []
    for test_file, lines in _test_patch_sections(spec):
        line = None
        for text in lines:
            m = _HUNK_HEADER.match(text)
            if m:
                line, reported = int(m.group(1)), False
                continue
            if line is None:
                continue
            if text.startswith(("+", "-")) and not reported:
                points.append((test_file, line))
                reported = True
            if not text.startswith("-"):
                line += 1
    return points


def _names(pairs: list) -> list:
    names, seen = [], set()
    for _, name in pairs:
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def test_case_pairs(spec) -> list:
    """``(test file, case)`` for every case the patch touches, each with the file
    that defines it: the cases it ADDS first — they exist only for this bug —
    then the existing ones it CHANGES. A patch can add a case that passes either
    way and tighten the one that exposes the bug (astropy 16005), so the changed
    ones are candidates too, not only a fallback for a patch that adds nothing."""
    added = _added_case_pairs(spec)
    keys = {(f, n.lower()) for f, n in added}
    return added + [(f, n) for f, n in _modified_case_pairs(spec)
                    if (f, n.lower()) not in keys]


def test_cases_modified(spec) -> list:
    """Names of the EXISTING test cases the reproducing-test patch changes.

    A PR very often fixes a bug by tightening an assertion that was already
    there — astropy#17483 moves `test_heapsize_P_limit`'s expected error from
    `writeto` to `from_columns` — and adds no new test. Such a patch has no
    added definition to find, so the test that exposes the bug would never be
    run; the hunk headers still name it.
    """
    return _names(_modified_case_pairs(spec))


def test_cases_added(spec) -> list:
    """Names of the test cases the reproducing-test patch introduces.

    The mined `test_selector` describes what the PR's own CI runs, which is
    often a whole suite or — worse — a different target than the one the patch
    touched. The patch itself names the cases exactly, and running exactly those
    is what makes a FAIL→PASS transition mean something.
    """
    return _names(_added_case_pairs(spec))[:20]


#: where the harness configures a CMake project, in each revision
CMAKE_BUILD_DIR = ".numagent-cmake"

#: top-level directories that hold a suite OUTSIDE the package it tests
_OUTSIDE_PACKAGE = ("tests", "test", "testing")


def test_command_for(language: str, test_file: str, case: str, package: str = "",
                     installed: bool = False, sanitizer: str = "", runner: str = "") -> str:
    """The command that runs exactly `case` from `test_file`, in `language`.

    Built from the patch rather than mined, because a mined `test_selector` is
    whatever the project's CI happens to run: a whole suite, or — as with Rust
    crates whose tests live beside the code — a different target altogether,
    which passes on both revisions and hides a perfectly good instance.
    """
    lang = (language or "").lower()
    # The TEST FILE decides how it runs, not the instance. opencv's fix is C++
    # and its reproducing test is modules/python/test/test_misc.py — a Python
    # test of its bindings — so a ctest target derived from the file name
    # ("test_misc") does not exist, and the run dies with exit 2 on both sides.
    if lang in ("c", "cpp") and test_file.endswith(".py"):
        lang, installed = "python", False
    if lang == "python":
        # pytest's OWN deprecations are about how the test file uses pytest —
        # never about the numerics under test. Today's pytest deprecates what an
        # older suite does routinely (a generator passed to parametrize), and a
        # project testing under `filterwarnings = error` then dies collecting,
        # on both revisions, before any test runs. -W outranks the ini filters
        # for that one category and leaves every other one in force — unlike
        # `-p no:warnings`, which switches the project's filters off entirely.
        quiet = "-W ignore::pytest.PytestDeprecationWarning"
        if sanitizer:
            # -s: UBSan writes to stderr, and pytest shows a PASSING test's
            # captured stderr to no one — the report is the whole signal
            quiet += " -s"
        if installed:
            # A compiled package is imported from its build, never from the
            # source root — so the test is addressed by MODULE path. `-k` picks
            # the case, since --pyargs takes no `::` node id.
            parts = test_file.removesuffix(".py").strip("/").split("/")
            # the top-level suite FIRST: scikit-image's `tests/skimage/restoration/
            # test_unwrap.py` mirrors the package's layout, and naming it
            # `skimage.restoration.test_unwrap` is "not found" on both revisions
            if parts[0] in _OUTSIDE_PACKAGE:
                start = None                   # jax's top-level tests/: no module
            elif package and package in parts[:-1]:
                start = parts.index(package)   # src/pkg/…, lib/matplotlib/… → pkg.…
            else:
                start = 0                      # the path IS the module path
            if start is None:
                # The tests are not part of the installed package, so there is
                # no module to name: `--pyargs tests.shape_poly_test` is "not
                # found" on both revisions. The file is run instead — still with
                # -I, and without --confcutdir: a suite kept outside its package
                # takes its fixtures from the root conftest.
                return (f"python -I -m pytest {test_file} -k {shlex.quote(case)} "
                        f"-q -rs {quiet}")
            module = ".".join(parts[start:])
            # -I (isolated mode) keeps `python -m` from prepending the cwd —
            # without it the un-built source tree shadows the revision's compiled
            # build and every test dies at `import`. Not PYTHONSAFEPATH: that only
            # exists from Python 3.11, and a 3.10 revision silently ignores it.
            # It is set HERE and not globally: a project's own build scripts
            # import their siblings by script dir, and isolation breaks them.
            #
            # --confcutdir closes the other way back in. The venv lives INSIDE
            # the revision, so pytest's conftest walk from the installed test
            # climbs through the source root — and falls back to the cwd for a
            # --pyargs module name besides. A root conftest that imports the
            # package (astropy's does) then loads the un-built tree before any
            # test runs. Cutting at the venv keeps every conftest the build
            # shipped and drops only those above it, which are source-tree files.
            #
            # Resolved at RUNTIME to the venv's own prefix rather than the literal
            # relative `.venv`: under build-in-loop the agent builds the venv and
            # it need not sit at `<cwd>/.venv`, so a relative `.venv` made pytest
            # hard-error ("--confcutdir must be a directory, given: .venv") on
            # BOTH revisions — every pandas/astropy instance then read F2F. The
            # revision's python is on PATH either way, so sys.prefix is the venv
            # root in both build modes (and absolute, so it survives a cd too).
            confcut = ("$(python -c 'import sys; print(sys.prefix)' "
                       "2>/dev/null || echo .venv)")
            qcase = shlex.quote(case)
            pyargs_run = (f"python -I -m pytest --pyargs {module} "
                          f'-k {qcase} -q -rs --confcutdir="$CC" {quiet}')
            # FILE-PATH fallback, taken ONLY when --pyargs could not COLLECT —
            # never on a test failure. --pyargs needs the test module importable
            # through the installed package, but a meson-python EDITABLE install
            # (the agent's `pip install -e .` under build-in-loop, pandas) exposes
            # only built modules, not the test .py files, so `--pyargs
            # pandas.tests.scalar...test_round` dies "module or package not found
            # (missing __init__.py?)" and collects nothing — while the same
            # command works under build-fixed's full install. The source tree IS
            # on disk, so the file runs directly (no --confcutdir: a path run
            # takes its fixtures from the project's own conftest). Gated on a
            # COLLECTION signature so a genuine fail->pass (pytest exit 1, the
            # bug) is never masked by a re-run.
            path_run = (f"python -I -m pytest {test_file} -k {qcase} -q -rs {quiet}")
            return (
                f'CC="{confcut}"; '
                f"__o=$({pyargs_run} 2>&1); __c=$?; "
                f'if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE '
                f'"module or package not found|no tests ran|file or directory not found|'
                f'ERROR: not found"; then {path_run}; '
                f'else printf "%s\\n" "$__o"; exit "$__c"; fi')
        # -k, not `file::case`: a node id without the class misses every test
        # that is a METHOD (`LaxTest::testTanhGradSymmetry`), and pytest then
        # reports "not found" — read as a stale selector on both revisions
        return f"python -m pytest {test_file} -k {shlex.quote(case)} -q -rs {quiet}"
    if lang == "rust":
        pkg = f"-p {package} " if package else ""
        return f"cargo test {pkg}{case} -- --nocapture"
    if lang == "go":
        directory = test_file.rsplit("/", 1)[0] if "/" in test_file else "."
        return f"go test ./{directory} -run '^{case}$' -v"
    if lang in ("javascript", "typescript"):
        # an empty case is a whole-file target: the change could not be placed
        # inside a named case, so every case in the file is the selection
        if runner == "tape":
            # a tape suite is a plain program: it runs itself, and there is no
            # case filter — the file IS the target. `npx mocha` on one collects
            # no tests at all, and the suite never runs.
            return f"node {test_file}"
        if runner == "node-test":
            return (f"node --test {test_file}"
                    + (f" --test-name-pattern {shlex.quote(case)}" if case else ""))
        if runner == "jest":
            return f"npx jest {test_file}" + (f" -t {shlex.quote(case)}" if case else "")
        if runner == "ava":
            return f"npx ava {test_file}" + (f" -m {shlex.quote(case)}" if case else "")
        return f"npx mocha {test_file}" + (f" -g {case!r}" if case else "")
    if lang in ("c", "cpp") and runner == "ctest":
        # The harness configured each revision in .numagent-cmake, so the test
        # target is built and run THERE — the same command on both revisions.
        # Left to the agent, this is where a comparison goes wrong: it built in
        # the buggy tree and then looked for that directory under fixed.
        target = test_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return (f"cmake --build {CMAKE_BUILD_DIR} --target {shlex.quote(target)} "
                f"-j$(nproc) && ctest --test-dir {CMAKE_BUILD_DIR} "
                f"-R {shlex.quote(target)} --output-on-failure")
    if lang == "java":
        cls = test_file.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return f"mvn -q test -Dtest={cls}#{case} -DfailIfNoTests=false"
    return ""

from __future__ import annotations

import re
import shlex
import sys
import shutil
from pathlib import Path

from numbugminer import repo as repo_mod

from . import shell

from .repro_cargo import apply_retargets, repair_cargo_workspace, resolves
from .repro_container import Container, write_file
from .repro_specs import (ReproSpec, git_apply, patch_paths_of, python_package,
                          test_files_of)

BUGGY_DIR = "/workspace/buggy"
FIXED_DIR = "/workspace/fixed"


TEST_REQUIREMENTS_FILES = (
    "build/test-requirements.txt", "test-requirements.txt", "test_requirements.txt",
    "requirements-test.txt", "requirements_test.txt", "requirements/test.txt",
    "requirements/tests.txt", "requirements/testing.txt", "tests/requirements.txt",
)


def install_test_requirements(container: Container) -> dict:
    report = {}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        found = container.run(
            "for f in " + " ".join(TEST_REQUIREMENTS_FILES)
            + '; do [ -f "$f" ] && echo "$f"; done', workdir=workdir, timeout_s=60)
        files = [f for f in (found.stdout or "").split() if f]
        if not files:
            report[rev] = {"files": []}
            continue
        whole = container.run("python -m pip install --no-cache-dir "
                              + " ".join(f"-r {shlex.quote(f)}" for f in files),
                              workdir=workdir, timeout_s=1800)
        failed = []
        if whole.exit_code != 0:
            each = container.run(
                "cat " + " ".join(shlex.quote(f) for f in files)
                + r" | sed -e 's/[[:space:]]#.*//' | grep -vE '^[[:space:]]*(#|-|$)'"
                + ' | while read -r req; do python -m pip install -q --no-cache-dir "$req"'
                + ' >/dev/null 2>&1 || echo "FAILED $req"; done',
                workdir=workdir, timeout_s=2400)
            failed = [ln[7:] for ln in (each.stdout or "").splitlines()
                      if ln.startswith("FAILED ")]
        report[rev] = {"files": files, "installed_whole": whole.exit_code == 0,
                       "failed": failed[:20]}
    return report


def install_test_deps(spec: ReproSpec, container: Container) -> dict:
    deps = [str(d).strip() for d in (spec.test_deps or ()) if str(d).strip()]
    if not deps:
        return {"installed": [], "skipped": "the spec names no test dependencies"}
    has_pip = container.run("command -v pip >/dev/null 2>&1 && echo pip",
                            workdir="/", timeout_s=60)
    if "pip" not in (has_pip.stdout or ""):
        return {"installed": [], "skipped": "no pip in this image"}
    r = container.run("pip install -q " + " ".join(shlex.quote(d) for d in deps),
                      workdir="/", timeout_s=900)
    if r.exit_code != 0:
        return {"installed": [], "requested": deps, "exit_code": r.exit_code,
                "error": (r.stderr or r.stdout or "").strip()[-300:]}
    return {"installed": deps}



#: STATIC era pinning: the packages whose version is known to change numerical
#: behaviour, pinned for every instance regardless of what it actually imports.
#: Deliberately short — each name costs a PyPI round trip per revision.
ERA_PINNED = ("numpy", "pandas", "matplotlib", "pyparsing", "llvmlite")
CONSTRAINTS_FILE = ".numagent-constraints.txt"

#: how the set of era-pinned packages is chosen. "static" uses ERA_PINNED for
#: every instance; "dynamic" reads it from the mined strategy's environment
#: block, so an instance is pinned for what IT depends on. Set per run with
#: NUMBUGBENCH_ERA_MODE, or per call with `era_pin_set(spec, mode=...)`.
ERA_MODE_STATIC = "static"
ERA_MODE_DYNAMIC = "dynamic"
#: "off" is the ablation: NO dependency is era-pinned (era_pin_set returns ()),
#: and the mined `environment.era_constraints` prose is withheld from the agent
#: (loop.reproduce). It measures reproduction with no historical-environment
#: guidance at all — distinct from "dynamic" (pin what the strategy names) and
#: "static" (pin the fixed five).
ERA_MODE_OFF = "off"
ERA_MODE_DEFAULT = ERA_MODE_STATIC

#: a pip_packages entry is prose unless it is a bare distribution name with an
#: optional extras group and an optional specifier: "llvmlite==0.44.0" is a
#: requirement, "numba (installed from the checked-out repo)" is a sentence.
_DIST = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*"
                   r"((?:[<>=!~]=?|===)[^;#]*)?\s*$")

#: never era-pin these from a strategy: the build backend resolves them itself,
#: and pinning them to the era breaks modern `pip install -e .`
_NEVER_PIN = frozenset({"pip", "setuptools", "wheel", "build", "setuptools-scm",
                        "setuptools_scm", "hatchling", "hatch-vcs", "meson",
                        "meson-python", "ninja", "cython", "cmake", "flit",
                        "flit-core", "poetry", "poetry-core", "versioningit"})


def era_mode(mode: str = "") -> str:
    """Which era-pinning policy this run uses."""
    import os

    chosen = (mode or os.environ.get("NUMBUGBENCH_ERA_MODE") or ERA_MODE_DEFAULT).strip().lower()
    return chosen if chosen in (ERA_MODE_STATIC, ERA_MODE_DYNAMIC, ERA_MODE_OFF) else ERA_MODE_DEFAULT


def strategy_requirements(spec) -> dict:
    """`{distribution: specifier}` from the mined strategy's environment block.

    `pip_packages` is written by the strategy generator and is free text: it
    holds bare names ("numpy"), real requirements ("llvmlite==0.44.0") and
    sentences ("numba (installed from the checked-out repo at the chosen git
    revisions)"). Only the first two forms are requirements; the rest is prose
    and is dropped rather than guessed at.
    """
    env = ((getattr(spec, "strategy", None) or {}).get("environment") or {})
    if not isinstance(env, dict):
        return {}
    found = {}
    for entry in (env.get("pip_packages") or []):
        m = _DIST.match(str(entry or ""))
        if not m:
            continue                            # prose, not a requirement
        name = m.group(1).lower()
        if name in _NEVER_PIN:
            continue
        found[name] = (m.group(2) or "").strip()
    return found


def declared_dependencies(container: Container, workdir: str = BUGGY_DIR) -> tuple:
    """The runtime dependencies the project itself declares, by name: its
    pyproject `[project].dependencies`, else setup.cfg `install_requires`, else
    setup.py `install_requires=[...]`, else requirements.txt. Build tools are
    left out, as for every other pin set."""
    reqs = list((_pyproject(container, workdir).get("project") or {}).get("dependencies") or [])
    if not reqs:
        import configparser
        cfg = configparser.ConfigParser()
        try:
            cfg.read_string(container.run("cat setup.cfg 2>/dev/null", workdir=workdir,
                                          timeout_s=60).stdout or "")
            reqs = [r for r in cfg.get("options", "install_requires", fallback="").splitlines()
                    if r.strip()]
        except configparser.Error:
            reqs = []
    if not reqs:
        text = container.run("cat setup.py 2>/dev/null", workdir=workdir, timeout_s=60).stdout or ""
        m = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.S)
        reqs = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)) if m else []
    if not reqs:
        text = container.run("cat requirements.txt 2>/dev/null", workdir=workdir,
                             timeout_s=60).stdout or ""
        reqs = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "-"))]
    names = (_requirement_name(r) for r in reqs)
    return tuple(dict.fromkeys(n for n in names if n and n not in _NEVER_PIN))


def era_pin_set(spec, mode: str = "", container: Container | None = None,
                workdir: str = BUGGY_DIR) -> tuple:
    """The packages to era-pin for `spec`, under the active policy.

    STATIC returns ERA_PINNED — the same five names for every instance, which
    is what every arm before this switch ran with. It is a named ablation arm,
    not the default, and the only place a fixed list of projects remains.

    DYNAMIC returns what the strategy says the instance needs. That set can be
    SMALLER than the static one (a strategy that names only numpy drops the
    other four) and can be LARGER (it picks up scs, llvmlite, torch — whatever
    the evidence documents). Both directions are the point: the static list is
    a guess that is wrong in both directions, and this makes the cost of that
    guess measurable instead of assumed.
    """
    active = era_mode(mode)
    if active == ERA_MODE_OFF:
        return ()                               # ablation: pin nothing
    if active == ERA_MODE_STATIC:
        return ERA_PINNED
    own = (getattr(spec, "repo", "") or "").rsplit("/", 1)[-1].lower()
    wanted = tuple(n for n in strategy_requirements(spec) if n != own)
    # Fall back AFTER dropping the subject, not before: 21 of 284 strategies
    # name only the project itself ("pip_packages": ["numba"]), and filtering
    # that leaves nothing. Returning an empty set there would silently switch
    # era pinning OFF for those instances rather than choosing a policy, which
    # is the one outcome neither arm is supposed to measure. The fallback is
    # what the PROJECT declares (numba declares llvmlite), not a fixed list.
    if wanted:
        return wanted
    declared = declared_dependencies(container, workdir) if container is not None else ()
    return tuple(n for n in declared if n != own)


def _requirement_spec(requirement: str) -> str:
    m = re.match(r"\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(?:\[[^\]]*\])?\s*(.*)", requirement or "")
    return (m.group(1).strip() if m else "").split(";")[0].strip()


def _marker_applies(requirement: str, python_version: str) -> bool:
    """Does this requirement's environment marker hold for THIS interpreter?

    scipy's `[build-system] requires` names numpy five times, once per
    interpreter:

        numpy==1.22.4;        python_version<='3.10' and …!='PyPy'
        numpy==1.23.2;        python_version=='3.11' and …!='PyPy'
        numpy>=1.26.0,<1.27;  python_version=='3.12'
        numpy>=1.25.0;        python_version>='3.9' and …=='PyPy'
        numpy>=2.0.0.dev0;    python_version>='3.13'

    With the markers ignored every line looked applicable and the LAST one won,
    so a python:3.10 build of January-2024 scipy was pinned to numpy 2.0.0 —
    five months in its future — and its pre-2.0 C sources failed to compile
    (`PyArray_Descr has no member named 'elsize'`). A requirement that does not
    apply to the interpreter being built for must not constrain it.

    An unparseable or unknown marker is treated as applicable: constraining less
    is recoverable, dropping a requirement the build needs is not.
    """
    if ";" not in (requirement or ""):
        return True
    marker = requirement.split(";", 1)[1].strip()
    if not marker:
        return True
    version = (python_version or "").strip() or f"{sys.version_info[0]}.{sys.version_info[1]}"
    try:
        from packaging.markers import Marker

        return bool(Marker(marker).evaluate({
            "python_version": version,
            "python_full_version": version if version.count(".") > 1 else f"{version}.0",
            "platform_python_implementation": "CPython",
            "implementation_name": "cpython",
            "sys_platform": "linux",
            "platform_system": "Linux",
            "os_name": "posix",
        }))
    except Exception:  # noqa: BLE001 — an unreadable marker constrains nothing
        return True


def _satisfies(version: str, specifier: str) -> bool:
    """Does `version` meet a requirement's specifier ("" means anything does)?"""
    if not specifier:
        return True
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        return Version(version) in SpecifierSet(specifier, prereleases=True)
    except Exception:  # noqa: BLE001 — an unparseable spec constrains nothing
        return True


def era_version(package: str, before_iso: str, python_tag: str = "",
                timeout: float = 30.0, specifier: str = "") -> str:
    """The newest release of `package` published before `before_iso`.

    Restricted to releases with a wheel for `python_tag` (e.g. "cp311") when
    one is given: an era-correct numpy with no wheel for the image's Python
    would be compiled from source, which is its own way to fail.
    """
    import json as _json
    import urllib.error
    import urllib.request

    request = urllib.request.Request(f"https://pypi.org/pypi/{package}/json",
                                     headers={"User-Agent": "numagent-benchmark"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            releases = _json.load(resp).get("releases") or {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return ""
    candidates = []
    for version, files in releases.items():
        if not files or not re.fullmatch(r"\d+(\.\d+)*", version):
            continue                                # skip pre-releases
        uploaded = min(f.get("upload_time_iso_8601") or "9999" for f in files)
        if uploaded >= before_iso:
            continue
        if python_tag and not any(f.get("python_version") in (python_tag, "py3")
                                  for f in files):
            continue
        if not _satisfies(version, specifier):
            continue
        candidates.append((tuple(int(x) for x in version.split(".")), version))
    if candidates:
        return max(candidates)[1]
    if not specifier:
        return ""
    # The era predates what the project REQUIRES (scipy's 2024 sources need
    # numpy 2's C API, released weeks later): the earliest version that
    # satisfies it is as close to the era as the requirement allows.
    usable = [(tuple(int(x) for x in v.split(".")), v) for v in releases
              if re.fullmatch(r"\d+(\.\d+)*", v) and releases[v]
              and _satisfies(v, specifier)
              and (not python_tag or any(f.get("python_version") in (python_tag, "py3")
                                         for f in releases[v]))]
    return min(usable)[1] if usable else ""


def commit_date(clone, commit: str) -> str:
    """ISO committer date of `commit` in the host clone, or ""."""
    r = shell.run(["git", "show", "-s", "--format=%cI", commit], cwd=clone)
    return (r.stdout or "").strip() if r.exit_code == 0 else ""


def apply_test_patch_with_blobs(clone, spec: ReproSpec, patch) -> dict:
    out = {"applied": False, "restored": [], "missing": [], "excluded": [], "error": ""}
    result = git_apply(clone, patch)
    if result.exit_code == 0:
        out["applied"] = True
        return out
    out["error"] = (result.stderr or "").strip()[:300]

    carried = set(test_files_of(spec))          # entries that HAVE textual hunks
    blobless = [p for p in patch_paths_of(spec.test_patch_path) if p not in carried]
    if not blobless or not spec.fixed_commit:
        return out
    out["excluded"] = blobless
    retry = git_apply(clone, patch, exclude=blobless)
    out["applied"] = retry.exit_code == 0
    if not out["applied"]:
        out["error"] = (retry.stderr or "").strip()[:300]
        return out
    r = shell.run(["git", "checkout", spec.fixed_commit, "--", *blobless], cwd=clone)
    if r.exit_code == 0:
        out["restored"] = list(blobless)
    else:                       # one bad path must not lose the other eighteen
        for path in blobless:
            one = shell.run(["git", "checkout", spec.fixed_commit, "--", path], cwd=clone)
            (out["restored"] if one.exit_code == 0 else out["missing"]).append(path)
    shell.run(["git", "reset", "--quiet"], cwd=clone)   # leave them unstaged
    return out


_DETECT_COMPILED = r"""python - <<'PY'
import glob, os
evidence = []
if os.path.exists("meson.build"):
    evidence.append("meson.build")
try:
    import tomllib
    requires = " ".join(tomllib.load(open("pyproject.toml", "rb"))
                        ["build-system"]["requires"]).lower()
except Exception:
    requires = ""
for tool in ("cython", "extension-helpers", "meson-python", "scikit-build",
             "pybind11", "pythran", "cffi"):
    if tool in requires:
        evidence.append("build-requires:" + tool)
try:
    import re as _re
    setup = open("setup.py").read()
    # `ext_modules=[]` is sympy SAYING it has no extensions: matching the bare
    # word built a venv, compiled nothing, and failed a pure-Python instance
    if _re.search(r"ext_modules\s*=\s*(?!\[\s*\])\S", setup) or "Extension(" in setup:
        evidence.append("setup.py:ext_modules")
except Exception:
    pass
sources = glob.glob("*/**/*.pyx", recursive=True)[:1]
if sources:
    evidence.append("cython-source:" + sources[0])
print("|".join(evidence))
PY"""


def detect_compiled_layout(container: Container, workdir: str) -> str:
    """Evidence that `workdir` holds a compiled package — "" when it is pure Python."""
    r = container.run(_DETECT_COMPILED, workdir=workdir, timeout_s=300)
    return (r.stdout or "").strip().splitlines()[-1].strip() if (r.stdout or "").strip() else ""


#: What building a compiled Python package needs on THIS image (Debian). The
#: mined list comes from the project's CI, which runs on another distribution
#: with other package names — scipy asks for `python3.12-dev` and
#: `libatlas-base-dev`, neither of which exists here — so the mined names are
#: best-effort extras on top of a toolchain that is known to work.
BUILD_TOOLCHAIN = ("build-essential", "gfortran", "pkg-config", "ninja-build",
                   "libopenblas-dev", "liblapack-dev")
#: A C/C++ instance builds with CMake, and the gcc image ships a compiler and
#: nothing else: PCL's run died on `cmake: command not found` before it could
#: configure anything.
CPP_TOOLCHAIN = ("build-essential", "cmake", "ninja-build", "pkg-config", "git")

#: `Could NOT find Eigen3` names a CMake package, not a Debian one. Only the
#: mapping is guesswork; what is MISSING comes from cmake itself.
APT_FOR_CMAKE_PACKAGE = {
    "boost": "libboost-all-dev", "eigen3": "libeigen3-dev", "eigen": "libeigen3-dev",
    "flann": "libflann-dev", "qhull": "libqhull-dev", "vtk": "libvtk9-dev",
    "openni2": "libopenni2-dev", "openni": "libopenni-dev", "png": "libpng-dev",
    "zlib": "zlib1g-dev", "gtest": "libgtest-dev", "gmock": "libgtest-dev",
    "openmp": "libomp-dev", "pcap": "libpcap-dev", "usb-1.0": "libusb-1.0-0-dev",
    "opencv": "libopencv-dev", "hdf5": "libhdf5-dev", "tbb": "libtbb-dev",
    "lapack": "liblapack-dev", "blas": "libopenblas-dev", "fftw3": "libfftw3-dev",
    "gsl": "libgsl-dev", "mpi": "libopenmpi-dev", "curl": "libcurl4-openssl-dev",
    "openssl": "libssl-dev", "sqlite3": "libsqlite3-dev", "libxml2": "libxml2-dev",
    "jpeg": "libjpeg-dev", "tiff": "libtiff-dev", "freetype": "libfreetype-dev",
    "glew": "libglew-dev", "glfw3": "libglfw3-dev", "x11": "libx11-dev",
    "qt5": "qtbase5-dev", "qt6": "qt6-base-dev", "yaml-cpp": "libyaml-cpp-dev",
    "protobuf": "libprotobuf-dev", "fmt": "libfmt-dev", "spdlog": "libspdlog-dev",
}
_CMAKE_MISSING = re.compile(
    # the lookahead keeps the other phrasing ("could not find A package
    # configuration file provided by …") from yielding the word "a"
    r"Could NOT find (?!a package configuration)([A-Za-z][\w+.-]*)"
    r"|Could not find a package configuration file provided by \"([A-Za-z][\w+.-]*)\"",
    re.I)


def configure_cmake_project(spec: ReproSpec, container: Container) -> dict:
    """Configure a C/C++ revision once, installing what CMake says it lacks.

    A CMake project states its dependencies by failing: "Could NOT find Eigen3".
    Each round installs what the last one named and tries again, so the agent
    inherits a configured tree instead of spending its budget discovering that
    the image has no Boost.
    """
    if (spec.language or "") not in ("c", "cpp"):
        return {"skipped": "not a C/C++ instance"}
    present = container.run("test -f CMakeLists.txt && echo yes", workdir=BUGGY_DIR,
                            timeout_s=60)
    if "yes" not in (present.stdout or ""):
        return {"skipped": "no CMakeLists.txt"}
    configure = ("cmake -S . -B .numagent-cmake -DCMAKE_BUILD_TYPE=Release "
                 "> .numagent-cmake.log 2>&1; code=$?; tail -c 2500 .numagent-cmake.log; "
                 "exit $code")
    installed, unmapped = [], []
    for _ in range(4):
        r = container.run(configure, workdir=BUGGY_DIR, timeout_s=1800)
        if r.exit_code == 0:
            # the FIXED revision too: a comparison needs both sides built, and
            # the packages this loop installed serve them equally
            other = container.run(configure, workdir=FIXED_DIR, timeout_s=1800)
            return {"configured": other.exit_code == 0, "buggy_configured": True,
                    "fixed_configured": other.exit_code == 0,
                    "installed": installed, "unmapped": unmapped,
                    "fixed_output": "" if other.exit_code == 0
                                    else (other.stdout or "")[-500:]}
        names = [n for pair in _CMAKE_MISSING.findall(r.stdout or "") for n in pair if n]
        wanted, seen = [], set()
        for name in names:
            package = APT_FOR_CMAKE_PACKAGE.get(name.lower())
            if not package:
                if name not in unmapped:
                    unmapped.append(name)
            elif package not in installed and package not in seen:
                seen.add(package)
                wanted.append(package)
        if not wanted:
            return {"configured": False, "installed": installed, "unmapped": unmapped,
                    "output": (r.stdout or "")[-800:]}
        container.run("apt-get update -qq", workdir="/", timeout_s=900)
        for package in wanted:
            got = container.run("DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
                                + shlex.quote(package), workdir="/", timeout_s=1800)
            if got.exit_code == 0:
                installed.append(package)
    return {"configured": False, "installed": installed, "unmapped": unmapped}





#: dpkg's own summary of which packages left the system broken
_POSTINST_FAILED = re.compile(r"Errors were encountered while processing:\s*\n((?:\s+\S+\n?)+)")


def install_system_packages(spec: ReproSpec, container: Container) -> dict:
    """apt-install the libraries a compiled instance needs to build at all."""
    if (spec.language or "") in ("c", "cpp"):
        # NOT the Python toolchain: its gfortran cannot configure in the gcc
        # image (the image builds GCC into /usr/local, so the Debian package's
        # post-install step fails), and one failed post-install leaves dpkg
        # broken for every install after it — cmake included.
        wanted = list(CPP_TOOLCHAIN)
    else:
        wanted = list(BUILD_TOOLCHAIN) if spec.needs_build else []
    wanted += [p for p in (spec.system_packages or ()) if p and p not in wanted]
    if not wanted:
        return {"installed": [], "skipped": "the instance names no system packages"}
    has_apt = container.run("command -v apt-get >/dev/null 2>&1 && echo apt",
                            workdir="/", timeout_s=60)
    if "apt" not in (has_apt.stdout or ""):
        return {"installed": [], "skipped": "no apt in this image"}

    # A failed index update makes EVERY install fail, and a transient one made a
    # whole C++ run look like an image without apt: it is retried, and whatever
    # it says is kept, so the next reader does not have to guess.
    update = container.run("apt-get update -qq", workdir="/", timeout_s=900)
    if update.exit_code != 0:
        update = container.run("apt-get update", workdir="/", timeout_s=900)
    install = "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
    r = container.run(install + " ".join(shlex.quote(p) for p in wanted),
                      workdir="/", timeout_s=1800)
    if r.exit_code == 0:
        return {"installed": wanted}
    # A bulk install that dies partway leaves dpkg half-configured, and then
    # EVERY later install fails with "Sub-process /usr/bin/dpkg returned an
    # error code (1)" — which is how a C++ run ended up with no toolchain at
    # all, cmake included. Repair the state before retrying one by one.
    # A package whose post-install keeps failing (gfortran here) is REMOVED, not
    # reconfigured: `dpkg --configure -a` runs the same failing script again and
    # leaves the state broken, so every later install fails with it.
    broken = _POSTINST_FAILED.search((r.stderr or "") + (r.stdout or ""))
    if broken:
        names = [n for n in broken.group(1).split() if n]
        if names:
            container.run("DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq --purge "
                          + " ".join(shlex.quote(n) for n in names)
                          + " || dpkg --remove --force-remove-reinstreq "
                          + " ".join(shlex.quote(n) for n in names),
                          workdir="/", timeout_s=900)
            wanted = [p for p in wanted if p not in names]
    container.run("dpkg --configure -a; DEBIAN_FRONTEND=noninteractive "
                  "apt-get install -f -y -qq", workdir="/", timeout_s=900)
    # One name this image does not have must not cost the whole toolchain.
    out = {"installed": [], "unavailable": [], "errors": {},
           "bulk_error": ((r.stderr or "") + (r.stdout or "")).strip()[-600:]}
    for package in wanted:
        one = container.run(install + shlex.quote(package), workdir="/", timeout_s=900)
        if one.exit_code == 0:
            out["installed"].append(package)
        else:
            out["unavailable"].append(package)
            out["errors"][package] = ((one.stderr or one.stdout or "").strip()[-200:]
                                      or f"exit {one.exit_code}")
    if update.exit_code != 0:
        out["apt_update"] = (update.stderr or update.stdout or "").strip()[-300:]
    return out


#: `ModuleNotFoundError: No module named 'tempita'` — a build-time helper the
#: project forgot to declare. pip would have supplied it through build
#: isolation; --no-build-isolation is what makes it our problem.
_MISSING_MODULE = re.compile(r"No module named ['\"]([A-Za-z_][\w.]*)['\"]")


_PIN_CONFLICT = re.compile(
    r"The user requested (?:\(constraint\)\s*)?([A-Za-z0-9][\w.-]*)==")


def _required_specifier(log: str, name: str) -> str:
    """What the revision requires of `name`, from pip's conflict report.

    pip writes it both as `llvmlite<0.46,>=0.45.0dev0` and as
    `llvmlite<0.46 and >=0.45.0dev0`; every clause of every mention is kept.
    """
    clause = r"(?:[<>=!~]=?|===)\s*[\w.*+!-]+"
    found = re.findall(rf"depends on {re.escape(name)}\s*({clause}(?:\s*(?:,|\band\b)\s*{clause})*)",
                       log, re.I)
    parts = {re.sub(r"\s+", "", p) for f in found for p in re.split(r",|\band\b", f) if p.strip()}
    return ",".join(sorted(parts))

_NO_SCM_VERSION = re.compile(r"unable to detect version|setuptools[-_]scm", re.I)
_PRETEND_VERSION = "SETUPTOOLS_SCM_PRETEND_VERSION=0+numagent "

#: GCC 14 turned these long-standing warnings into ERRORS by default, so C code
#: that compiled for years under GCC 13 — a 2024 astropy's wcslib wrapper, for
#: one — no longer does in today's image. -fpermissive turns them back into the
#: warnings they were when the revision was written; nothing else changes.
_STRICT_C_WARNINGS = ("incompatible-pointer-types|int-conversion|implicit-function-"
                      "declaration|implicit-int|return-mismatch|declaration-missing-"
                      "parameter-type")
_STRICT_C_ERROR = re.compile(rf"error: .*\[-W(?:{_STRICT_C_WARNINGS})\]")
#: compiler-flag remedies are (VARIABLE, flags) pairs so they ACCUMULATE —
#: two `CFLAGS="…"` prefixes on one command keep only one of them
_PERMISSIVE_C = (("CFLAGS", "-fpermissive"),)
#: UndefinedBehaviorSanitizer, recoverable: each report is printed and the run
#: continues, so a test still produces its verdict and the report rides along
_UBSAN = (("CFLAGS", "-fsanitize=undefined"), ("CXXFLAGS", "-fsanitize=undefined"),
          ("LDFLAGS", "-fsanitize=undefined"))


def _env_prefix(remedies: dict) -> str:
    """The shell prefix for a build: flag remedies exported and accumulated per
    variable first, then the plain `VAR=value ` assignments."""
    flags, plain = {}, []
    for value in remedies.values():
        if isinstance(value, tuple):
            for var, flag in value:
                flags.setdefault(var, []).append(flag)
        else:
            plain.append(value)
    exports = "".join(f'export {var}="${{{var}:-}} {" ".join(fs)}"; '
                      for var, fs in flags.items())
    return exports + "".join(plain)


def _sanitizer_linked(container: Container, workdir: str, package: str) -> dict:
    """Did the sanitizer make it into the build — are the extensions linked to it?"""
    found = container.run(
        f"find .venv -path '*{package}*' -name '*.so' 2>/dev/null | head -200 "
        f"| xargs -r ldd 2>/dev/null | grep -c libubsan", workdir=workdir, timeout_s=300)
    count = int((found.stdout or "0").strip().splitlines()[-1] or 0) \
        if (found.stdout or "").strip() else 0
    return {"kind": "undefined", "linked_extensions": count, "ok": count > 0}


def _strict_c_error(container: Container, workdir: str, excerpt: str) -> bool:
    """Did the build die on one of GCC 14's promoted warnings?

    Asked of the WHOLE log: the excerpt a build returns is its first error block
    and its tail, and a parallel build's compiler error can sit in neither.
    """
    if _STRICT_C_ERROR.search(excerpt or ""):
        return True
    found = container.run(f"grep -qE 'error: .*\\[-W({_STRICT_C_WARNINGS})\\]' "
                          ".numagent-build.log && echo yes",
                          workdir=workdir, timeout_s=120)
    return "yes" in (found.stdout or "")


#: A pure-Python package whose compiled half ships as a SEPARATE wheel, at a
#: version the revision itself declares:
#: package -> (companion wheel, file in the revision, regex for its version)
#: Deliberately EMPTY: the harness holds no knowledge of particular projects. It
#: held one entry (jax -> jaxlib); a missing companion now surfaces as
#: `No module named jaxlib` and the agent pins it through `rebuild` (GPT-5.4-nano
#: reproduced 11 jax instances on a path that never consulted this table).
COMPANION_WHEELS: dict = {}


def _version_key(version: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", version)[:4])


#: build backends that derive the package's version from git
_VCS_VERSIONING = re.compile(r"setuptools[-_]scm|hatch[-_]vcs|versioningit|"
                             r"setuptools[-_]git[-_]versioning|dunamai", re.I)


def _vcs_versioned(container: Container, workdir: str) -> bool:
    """Does this checkout take its version from git (and so need git to build)?

    mpmath's `_version.py` is not in the repository: setuptools-scm writes it at
    build time from the tags. A `-slim` image ships no git, the build cannot
    find a version, the file is never written, and `import mpmath` fails on
    both revisions with "No module named 'mpmath._version'".
    """
    doc = _pyproject(container, workdir)
    requires = " ".join((doc.get("build-system") or {}).get("requires") or [])
    tool = doc.get("tool") or {}
    if _VCS_VERSIONING.search(requires) or "setuptools_scm" in tool \
            or ((tool.get("hatch") or {}).get("version") or {}).get("source") == "vcs":
        return True
    setup = container.run("grep -l use_scm_version setup.py 2>/dev/null",
                          workdir=workdir, timeout_s=60)
    return bool((setup.stdout or "").strip())


def _ensure_git(container: Container) -> dict:
    """Install git where the image has none, and re-trust the checkouts.

    The `safe.directory` setting that lets git read a `docker cp`'d tree (owned
    by the host's uid) was applied before git existed on a slim image — a no-op
    — so it is applied again once git is there.
    """
    if container.run("command -v git", workdir="/", timeout_s=60).exit_code == 0:
        return {"git": "present"}
    got = container.run("apt-get update -qq && DEBIAN_FRONTEND=noninteractive "
                        "apt-get install -y -qq --no-install-recommends git",
                        workdir="/", timeout_s=900)
    container.run("git config --global --add safe.directory '*'", workdir="/", timeout_s=60)
    return {"git": "installed" if got.exit_code == 0 else "install failed"}


def provision_pure_python(spec: ReproSpec, container: Container, era: str = "") -> dict:
    """Give a pure-Python revision the runtime it was written against.

    A compiled instance gets a venv, a build and era pins from build_revisions;
    a pure-Python one got nothing — its dependencies were whatever the agent
    thought to install, at today's versions. For jax that meant today's jaxlib
    against 2024 source (refused), a guessed jaxlib that never reached the test
    interpreter, or a numpy two years too new. Here the era is pinned for every
    later pip, the revision's declared companion wheel is installed, and the
    package's own dependencies are installed WITHOUT the package, so each
    revision's source tree is still what answers `import`.
    """
    if spec.needs_build or (spec.language or "python") != "python":
        return {"skipped": "not a pure-Python instance"}
    package = python_package(spec)
    tag = "cp" + (spec.python_version or "").replace(".", "")
    pins = {}
    if era:
        wanted = era_pin_set(spec, container=container)
        # a dynamic set carries the strategy's own specifiers ("llvmlite==0.44.0"),
        # which narrow the era pick the same way an explicit override does
        declared = strategy_requirements(spec) if era_mode() == ERA_MODE_DYNAMIC else {}
        for dependency in wanted:
            if dependency != package.lower():
                version = era_version(dependency, era, tag,
                                      specifier=declared.get(dependency, ""))
                if version:
                    pins[dependency] = version
    companion = {}
    if package in COMPANION_WHEELS:
        wheel, path, pattern = COMPANION_WHEELS[package]
        declared = []
        for workdir in (BUGGY_DIR, FIXED_DIR):
            text = container.run(f"cat {shlex.quote(path)} 2>/dev/null", workdir=workdir,
                                 timeout_s=60).stdout or ""
            m = re.search(pattern, text)
            if m:
                declared.append(m.group(1))
        # the higher of the two: a fix may raise the floor, and the lower
        # revision still accepts it (it only refuses one newer than itself)
        wanted = max(declared, key=_version_key) if declared else ""
        companion = {"wheel": wheel, "declared": declared, "wanted": wanted}
    for workdir in (BUGGY_DIR, FIXED_DIR):
        body = "".join(f"{k}=={v}\n" for k, v in pins.items())
        if companion.get("wanted"):
            body += f"{companion['wheel']}=={companion['wanted']}\n"
        if body:
            write_file(container, f"{workdir}/{CONSTRAINTS_FILE}", body)
    report = {"era_pins": pins, "companion": companion}
    if companion.get("wanted"):
        got = container.run(f"python -m pip install --no-cache-dir "
                            f"{companion['wheel']}=={companion['wanted']}",
                            workdir=BUGGY_DIR, timeout_s=1800)
        if got.exit_code != 0:
            # no wheel of the declared version for this Python: the era's own,
            # re-pinned so nothing later moves it
            fallback = era_version(companion["wheel"], era, tag) if era else ""
            companion["fallback"] = fallback
            if fallback:
                for workdir in (BUGGY_DIR, FIXED_DIR):
                    container.run(f"sed -i '/^{companion['wheel']}==/d' {CONSTRAINTS_FILE}; "
                                  f"echo '{companion['wheel']}=={fallback}' >> {CONSTRAINTS_FILE}",
                                  workdir=workdir, timeout_s=60)
                got = container.run(f"python -m pip install --no-cache-dir "
                                    f"{companion['wheel']}=={fallback}",
                                    workdir=BUGGY_DIR, timeout_s=1800)
        companion["installed"] = got.exit_code == 0
    if pins:
        # every era pin, not one package singled out by name
        container.run("python -m pip install --no-cache-dir "
                      + " ".join(f"{shlex.quote(k)}=={shlex.quote(v)}" for k, v in pins.items()),
                      workdir=BUGGY_DIR, timeout_s=1800)
    if _vcs_versioned(container, BUGGY_DIR):
        report.update(_ensure_git(container))
    # Installing the revision (then removing it) also BUILDS it in place, which
    # is what writes a setuptools-scm version file into the source tree.
    deps, pretended = {}, False
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        install = (f"python -m pip install --no-cache-dir . && "
                   f"python -m pip uninstall -y {shlex.quote(package)}")
        r = container.run(install, workdir=workdir, timeout_s=2400)
        if r.exit_code != 0 and _NO_SCM_VERSION.search((r.stdout or "") + (r.stderr or "")):
            r = container.run(_PRETEND_VERSION + install, workdir=workdir, timeout_s=2400)
            pretended = True
        deps[rev] = r.exit_code == 0
    report["dependencies_installed"] = deps
    report["pretend_version"] = pretended
    # The project's declared TEST extra, as a compiled instance gets after its
    # build. mpmath's names matplotlib (its plotting tests skip without it) and
    # hypothesis (its conftest imports it, so pytest cannot even start).
    extras = []
    for workdir in (BUGGY_DIR, FIXED_DIR):
        for requirement in (_test_extra_requirements(container, workdir)
                            + _addopts_plugins(container, workdir)
                            + _importorskip_requirements(container, workdir, spec, package)):
            if requirement not in extras:
                extras.append(requirement)
    # never filtered here: see build_revisions — a plugin the project's addopts
    # names is what lets pytest start at all
    # a self-reference ("mpmath[tests]" inside [develop]) would reinstall the package
    extras = [r for r in extras if not re.match(rf"{re.escape(package)}\b", r, re.I)]
    extras = _era_plugins(extras, era, tag)
    if extras:
        got = container.run("python -m pip install --no-cache-dir "
                            + " ".join(shlex.quote(r) for r in extras),
                            workdir=BUGGY_DIR, timeout_s=1800)
        if got.exit_code != 0:          # one bad pin must not cost the rest
            for requirement in extras:
                container.run(f"python -m pip install --no-cache-dir {shlex.quote(requirement)}",
                              workdir=BUGGY_DIR, timeout_s=900)
    report["test_extras"] = extras[:20]
    imports = {}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        where = container.run(f'python -c "import {package} as m; print(m.__file__)" 2>&1 '
                              f"| tail -1", workdir=workdir, timeout_s=300)
        imports[rev] = (where.stdout or "").strip()[-200:]
    report["imports_from"] = imports
    report["ok"] = all(f"{workdir}/" in imports[rev] for rev, workdir in
                       (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)))
    return report


def _pyproject(container: Container, workdir: str) -> dict:
    """The revision's ``pyproject.toml``, parsed on the HOST.

    Parsing it inside the container meant `import tomllib` in the revision's
    venv — and tomllib only exists from Python 3.11. On a 3.10 revision the
    import failed silently, every requirement read came back empty, and a
    Cython project was built with no Cython: the build then looks for the
    generated `.c` files a git checkout never has. The host's parser answers
    the same question whatever Python the revision runs.
    """
    try:
        import tomllib
    except ModuleNotFoundError:          # host older than 3.11
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            return {}
    read = container.run("cat pyproject.toml 2>/dev/null", workdir=workdir, timeout_s=120)
    try:
        return tomllib.loads(read.stdout or "")
    except tomllib.TOMLDecodeError:
        return {}


def _requirement_name(requirement: str) -> str:
    """The normalized distribution name a requirement string names (PEP 503)."""
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    return re.sub(r"[-_.]+", "-", m.group(1)).lower() if m else ""


def _build_requirements(container: Container, workdir: str) -> list:
    """The project's own ``[build-system] requires``.

    Building with --no-build-isolation is what keeps the revision's compiled
    package in ITS venv rather than a throwaway one — but it also means nobody
    installs the build backend, so the project's declared requirements have to
    be installed by hand first.
    """
    requires = (_pyproject(container, workdir).get("build-system") or {}).get("requires") or []
    return [str(r).strip() for r in requires if str(r).strip()]


#: `fitsio = pytest.importorskip("fitsio")`: an optional package a test module
#: SKIPS without, which the project need not declare in any test extra.
_IMPORTORSKIP = re.compile(r"""importorskip\(\s*["']([A-Za-z_]\w*)""")


def _importorskip_requirements(container: Container, workdir: str, spec: ReproSpec,
                               package: str) -> list:
    """Top-level modules the PR's OWN test files `pytest.importorskip`.

    Only the reproducing test's files are read, never the whole suite: those are
    the tests whose transition is graded, and a skip there grades as nothing
    (astropy#19592's test_fitsio.py skipped without fitsio on both revisions).
    The package under test is excluded, so this can never install a released copy.
    """
    from .repro_specs import test_files_of
    files = [f for f in test_files_of(spec) if f.endswith(".py")]
    if not files:
        return []
    out = container.run("cat " + " ".join(shlex.quote(f) for f in files) + " 2>/dev/null",
                        workdir=workdir, timeout_s=120)
    return [m for m in dict.fromkeys(_IMPORTORSKIP.findall(out.stdout or ""))
            if m.lower() != package.lower()]


def _test_extra_requirements(container: Container, workdir: str) -> list:
    """The project's own declared TEST extra, if it has one.

    A project configures pytest in `pyproject.toml`, and those settings often
    name plugins: astropy's `addopts` carries `--doctest-rst`, which only exists
    once pytest-doctestplus is installed. Without it pytest refuses to start —
    "unrecognized arguments" — on both revisions, which is a setup failure that
    looks like the instance being broken.
    """
    pyproject = _pyproject(container, workdir)
    extras = (pyproject.get("project") or {}).get("optional-dependencies") or {}
    key = next((k for k in ("test", "tests", "testing", "dev") if k in extras), None)
    found = [str(r).strip() for r in (extras.get(key) or [])
             if str(r).strip() and not str(r).strip().startswith("#")]
    if found:
        return found
    # PEP 735 groups: xarray declares pytest-mypy-plugins — which its `addopts`
    # needs for `--mypy-pyproject-toml-file` — only in `[dependency-groups] dev`.
    groups = pyproject.get("dependency-groups") or {}
    key = next((k for k in ("test", "tests", "testing", "dev") if k in groups), None)
    found = _dependency_group(groups, key)
    if key == "dev":
        # a dev group is also the docs stack, the linters and `xarray[complete]`;
        # only the test runner and its plugins are this instance's business
        found = [r for r in found
                 if re.match(r"(pytest[\w.-]*|hypothesis)\b", _requirement_name(r))]
    return found


_ADDOPTS_PLUGINS = (
    ("--doctest-rst", "pytest-doctestplus"),
    ("--doctest-plus", "pytest-doctestplus"),
    ("--text-file-format", "pytest-doctestplus"),
    ("--arraydiff", "pytest-arraydiff"),
    ("--remote-data", "pytest-remotedata"),
    ("--open-files", "pytest-openfiles"),
    ("--astropy-header", "pytest-astropy-header"),
    ("--filter-subpackage", "pytest-filter-subpackage"),
    ("--mypy-pyproject-toml-file", "pytest-mypy-plugins"),
    ("--mypy-only-local-stub", "pytest-mypy-plugins"),
    ("--benchmark", "pytest-benchmark"),
    ("--asyncio-mode", "pytest-asyncio"),
    ("--randomly", "pytest-randomly"),
    ("--timeout", "pytest-timeout"),
    ("--numprocesses", "pytest-xdist"),
    ("--cov", "pytest-cov"),
    ("--mpl", "pytest-mpl"),
    ("--slow", "pytest-skip-slow"),
)


def _addopts(container: Container, workdir: str) -> str:
    """The project's pytest `addopts`, from wherever it configures pytest."""
    doc = _pyproject(container, workdir)
    addopts = ((doc.get("tool") or {}).get("pytest") or {}).get("ini_options", {}).get("addopts", "")
    if isinstance(addopts, list):
        addopts = " ".join(str(a) for a in addopts)
    # setup.cfg / pytest.ini / tox.ini keep it as plain text under their own
    # section headers; reading them raw is enough to see which options are named
    raw = container.run("cat setup.cfg pytest.ini tox.ini 2>/dev/null",
                        workdir=workdir, timeout_s=60).stdout or ""
    return f"{addopts}\n{raw}"


def _addopts_plugins(container: Container, workdir: str) -> list:
    """Plugins the project's own pytest options require, in `addopts` order."""
    text = _addopts(container, workdir)
    found = []
    for option, dist in _ADDOPTS_PLUGINS:
        if option in text and dist not in found:
            found.append(dist)
    return found


def _era_plugins(requirements: list, era: str, python_tag: str = "") -> list:
    """Pin each UNPINNED pytest plugin to its release as of the revision's era.

    A plugin's options are what `addopts` names, and plugins drop them: xarray's
    2025 `addopts` passes `--mypy-only-local-stub`, which pytest-mypy-plugins 4.0
    removed, so today's release makes pytest refuse to start on both revisions.
    """
    if not era:
        return requirements
    out = []
    for requirement in requirements:
        name = _requirement_name(requirement)
        if (name.startswith("pytest-") and not _requirement_spec(requirement)
                and ";" not in requirement):
            version = era_version(name, era, python_tag)
            if version:
                requirement = f"{requirement}=={version}"
        out.append(requirement)
    return out


def _dependency_group(groups: dict, key, seen=()) -> list:
    """A PEP 735 dependency group's requirements, `include-group`s expanded."""
    out = []
    if key is None or key in seen:
        return out
    for item in groups.get(key) or []:
        if isinstance(item, dict):
            out += _dependency_group(groups, item.get("include-group"), (*seen, key))
        elif str(item).strip():
            out.append(str(item).strip())
    return out


#: installed into every built venv as a pytest11 entry point, so it loads for
#: the harness's test commands AND the agent's own, with nothing on the command line
_PYARGS_PLUGIN = "numagent_pyargs_conftests"


def install_pyargs_plugin(container: Container, workdir: str) -> bool:
    found = container.run(".venv/bin/python -c \"import sysconfig; "
                          "print(sysconfig.get_paths()['purelib'])\"",
                          workdir=workdir, timeout_s=120)
    purelib = (found.stdout or "").strip().splitlines()[-1:] or [""]
    if found.exit_code != 0 or not purelib[0].startswith("/"):
        return False
    site, dist = purelib[0], f"{purelib[0]}/{_PYARGS_PLUGIN}-0.dist-info"
    source = Path(__file__).with_name("pytest_pyargs_conftests.py").read_text()
    if container.run(f"mkdir -p {shlex.quote(dist)}", workdir=workdir,
                     timeout_s=60).exit_code != 0:
        return False
    write_file(container, f"{site}/{_PYARGS_PLUGIN}.py", source)
    write_file(container, f"{dist}/METADATA",
               f"Metadata-Version: 2.1\nName: {_PYARGS_PLUGIN.replace('_', '-')}\nVersion: 0\n")
    write_file(container, f"{dist}/entry_points.txt",
               f"[pytest11]\n{_PYARGS_PLUGIN} = {_PYARGS_PLUGIN}\n")
    return True


def _venv_owns_programs(container: Container, workdir: str,
                        programs=("meson", "cython")) -> dict:
    out = {}
    for program in programs:
        where = container.run(f'PATH="{workdir}/.venv/bin:$PATH" command -v {program}',
                              workdir=workdir, timeout_s=60)
        path = (where.stdout or "").strip().splitlines()[-1:] or [""]
        if where.exit_code != 0 or path[0].startswith(f"{workdir}/.venv/"):
            continue                      # absent (nothing to place) or already ours
        shown = container.run(f"{program} --version 2>&1", workdir=workdir, timeout_s=120)
        # "Cython version 3.3.0", "1.12.0" — the number, wherever it sits
        found = re.search(r"\d+(?:\.\d+)+", (shown.stdout or "") + (shown.stderr or ""))
        pinned = f"{program}=={found.group(0)}" if found else program
        got = container.run(".venv/bin/pip install --no-cache-dir --ignore-installed "
                            f"--no-deps {shlex.quote(pinned)}", workdir=workdir, timeout_s=900)
        out[program] = pinned if got.exit_code == 0 else f"{pinned}: failed"
    return out


def _venv_home(workdir: str) -> str:
    """Where a revision's build venv really lives: beside the checkout, not in it."""
    return f"/workspace/.venvs/{workdir.rstrip('/').rsplit('/', 1)[-1]}"


def ensure_interpreter(container: Container, version: str) -> str:
    """Absolute path to a CPython `version` (e.g. "3.11") in the container, or "".

    A `python:3.10-slim` image has no python3.11, so one that is absent is
    installed with uv, which ships standalone CPython builds for any slim image.
    """
    have = container.run(f"command -v python{shlex.quote(version)}", workdir="/", timeout_s=60)
    found = (have.stdout or "").strip().splitlines()[-1:]
    if have.exit_code == 0 and found and found[0].startswith("/"):
        return found[0]
    got = container.run(f"python -m pip install -q uv >/dev/null 2>&1; "
                        f"uv python install {shlex.quote(version)} >/dev/null 2>&1; "
                        f"uv python find {shlex.quote(version)}", workdir="/", timeout_s=900)
    found = (got.stdout or "").strip().splitlines()[-1:]
    return found[0] if got.exit_code == 0 and found and found[0].startswith("/") else ""


#: "numpy<2.4", "numpy == 2.3.5", "llvmlite>=0.44,<0.45": a dependency and its specifier
_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*((?:[<>=!~]=?|===).*)?$")


def parse_pins(pins, package: str = "") -> dict:
    """`["numpy<2.4"]` -> `{"numpy": "<2.4"}`; the package under test is never pinned."""
    out = {}
    for pin in pins or ():
        m = _PIN.match(str(pin))
        if m and m.group(1).lower().replace("_", "-") != package.lower().replace("_", "-"):
            out[m.group(1).lower()] = (m.group(2) or "").replace(" ", "")
    return out


#: what the agent may declare an instance to be when it builds a revision itself
#: (build-mode agent). "" keeps the harness's own classification.
RECIPES = ("compiled", "pure")


#: top-level directories that hold a project's tooling, not its package
_NOT_THE_PACKAGE = {"tests", "test", "testing", "doc", "docs", "benchmarks", "benchmark",
                    "bench", "asv_bench", "asv_benchmarks", "tools", "ci", "build",
                    "build_tools", "maint_tools", "examples", "scripts", "requirements"}

_DISCOVER_PACKAGES = r"""python - <<'PY'
import os
skip = set(%r)
for base in (".", "src", "lib", "python"):
    if not os.path.isdir(base):
        continue
    found = []
    for d in sorted(os.listdir(base)):
        path = os.path.join(base, d)
        if (d.startswith((".", "_")) or d.lower() in skip or not os.path.isdir(path)
                or not os.path.isfile(os.path.join(path, "__init__.py"))):
            continue
        n = sum(len([f for f in fs if f.endswith((".py", ".pyx"))]) for _, _, fs in os.walk(path))
        found.append(f"{d} {n}")
    if found:
        print("\n".join(found))
        break
PY"""


def discover_import_name(container: Container, workdir: str, repo: str) -> str:
    """The top-level package this checkout installs, read from the checkout.

    Replaces a hand-written table of repositories whose import name is not their
    own (scikit-learn is `sklearn`): the package is the top-level directory with
    an `__init__.py` — at the root or under src/, lib/, python/ — that is not
    the project's tooling. A name matching the repository wins; otherwise the
    largest such package does. "" when the layout says nothing.
    """
    # tooling is skipped INSIDE the scan: scikit-image's root holds only tests/
    # (with an __init__.py), and stopping there left src/skimage undiscovered
    got = container.run(_DISCOVER_PACKAGES % sorted(_NOT_THE_PACKAGE), workdir=workdir,
                        timeout_s=120)
    candidates = []
    for line in (got.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit() and parts[0].lower() not in _NOT_THE_PACKAGE:
            candidates.append((parts[0], int(parts[1])))
    if not candidates:
        return ""
    norm = lambda s: re.sub(r"[-_.]", "", s.lower())  # noqa: E731
    own = norm(repo.rsplit("/", 1)[-1])
    for name, _ in candidates:
        if norm(name) == own:
            return name
    return max(candidates, key=lambda c: c[1])[0]


def imports_from(container: Container, workdir: str, package: str) -> str:
    """Where `workdir`'s own interpreter imports `package` from, run from / so the
    source tree cannot answer by being the current directory."""
    if not package.isidentifier():
        return ""
    got = container.run(f'{workdir}/.venv/bin/python -c '
                        f'"import {package} as m; print(m.__file__)" 2>&1 | tail -1',
                        workdir="/", timeout_s=300)
    return (got.stdout or "").strip()[-300:]


def owns_import(location: str, revision: str) -> bool:
    """Is `location` this revision's own code: its source tree (an editable
    install) or its own venv (`/workspace/.venvs/<revision>/…`)?"""
    workdir = FIXED_DIR if revision == "fixed" else BUGGY_DIR
    return (location.startswith(f"{workdir}/")
            or location.startswith(f"{_venv_home(workdir)}/"))


def _build_pure_venv(spec: ReproSpec, container: Container, revision: str,
                     interpreter: str, era: str, overrides: dict) -> dict:
    """A pure-Python revision in a venv of its OWN, with nothing inherited.

    provision_pure_python installs into the image's interpreter, which both
    revisions share; the fallback in rebuild_revision makes a venv but leaves out
    what provisioning added (jax's companion jaxlib, the test extras, the plugins
    `addopts` names, the setuptools-scm fallback). This does all of it inside the
    venv, so the two revisions share nothing but the image.
    """
    workdir = FIXED_DIR if revision == "fixed" else BUGGY_DIR
    package = python_package(spec)
    tag = "cp" + (spec.python_version or "").replace(".", "")
    base = era_pin_set(spec, container=container, workdir=workdir)
    declared = strategy_requirements(spec) if era_mode() == ERA_MODE_DYNAMIC else {}
    merged = {**declared, **overrides}          # an explicit override always wins
    pins = {d: v for d in base + tuple(o for o in merged if o not in base)
            if d != package.lower() and (era or d in merged)
            and (v := era_version(d, era or "9999", tag, specifier=merged.get(d, "")))}
    companion = {}
    if package in COMPANION_WHEELS:
        wheel, path, pattern = COMPANION_WHEELS[package]
        text = container.run(f"cat {shlex.quote(path)} 2>/dev/null", workdir=workdir,
                             timeout_s=60).stdout or ""
        m = re.search(pattern, text)
        companion = {"wheel": wheel, "wanted": m.group(1) if m else ""}
        if companion["wanted"]:
            pins[wheel] = companion["wanted"]
    write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
               "".join(f"{k}=={v}\n" for k, v in pins.items()))
    venv = _venv_home(workdir)
    made = container.run(f"rm -rf {venv} && {interpreter} -m venv {venv} && rm -rf .venv && "
                         f"ln -s {venv} .venv && .venv/bin/pip install -q --no-cache-dir "
                         f"--upgrade pip setuptools wheel",
                         workdir=workdir, timeout_s=900)
    if made.exit_code != 0:
        return {"built": False, "exit_code": made.exit_code, "era_pins": pins,
                "error": (made.stderr or made.stdout or "").strip()[-300:] or "venv failed"}
    if companion.get("wanted"):
        got = container.run(f".venv/bin/pip install --no-cache-dir "
                            f"{companion['wheel']}=={companion['wanted']}",
                            workdir=workdir, timeout_s=1800)
        if got.exit_code != 0:
            # no wheel of the declared version for this Python: the era's own
            fallback = era_version(companion["wheel"], era, tag) if era else ""
            companion["fallback"] = fallback
            if fallback:
                pins[companion["wheel"]] = fallback
                write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
                           "".join(f"{k}=={v}\n" for k, v in pins.items()))
                got = container.run(f".venv/bin/pip install --no-cache-dir "
                                    f"{companion['wheel']}=={fallback}",
                                    workdir=workdir, timeout_s=1800)
        companion["installed"] = got.exit_code == 0
    install = ".venv/bin/pip install --no-cache-dir -e . > .numagent-build.log 2>&1; " \
              "code=$?; cat .numagent-build.log; exit $code"
    built = container.run(install, workdir=workdir, timeout_s=2400)
    pretended = False
    if built.exit_code != 0 and _NO_SCM_VERSION.search(built.stdout or ""):
        built = container.run(_PRETEND_VERSION + install, workdir=workdir, timeout_s=2400)
        pretended = True
    extras, requested = [], []
    if built.exit_code == 0:
        # What the AGENT named in `pins` is installed, not only constrained: a
        # constraints file limits versions and installs nothing, so jax#25791's
        # three rebuilds "adding absl-py" each left it missing. A test dependency
        # nobody named is still the agent's to notice and fix.
        requested = [f"{k}=={pins[k]}" if k in pins else f"{k}{overrides[k]}"
                     for k in overrides]
        if requested:
            container.run(".venv/bin/pip install --no-cache-dir "
                          + " ".join(shlex.quote(r) for r in requested),
                          workdir=workdir, timeout_s=1800)
        for requirement in (_test_extra_requirements(container, workdir)
                            + _addopts_plugins(container, workdir)
                            + _importorskip_requirements(container, workdir, spec, package)):
            if requirement not in extras:
                extras.append(requirement)
        # a self-reference ("mpmath[tests]") would reinstall the package
        extras = [r for r in extras if not re.match(rf"{re.escape(package)}\b", r, re.I)]
        extras = _era_plugins(extras, era, tag) if era else extras
        if extras:
            got = container.run(".venv/bin/pip install --no-cache-dir "
                                + " ".join(shlex.quote(r) for r in extras),
                                workdir=workdir, timeout_s=1800)
            if got.exit_code != 0:      # one bad pin must not cost the rest
                for requirement in extras:
                    container.run(f".venv/bin/pip install --no-cache-dir {shlex.quote(requirement)}",
                                  workdir=workdir, timeout_s=900)
    return {"built": built.exit_code == 0, "exit_code": built.exit_code,
            "era_pins": pins, "companion": companion, "test_extras": extras[:20],
            "installed_as_requested": requested,
            "pretend_version": pretended,
            "pyargs_plugin": install_pyargs_plugin(container, workdir) if built.exit_code == 0 else False,
            "output": _build_excerpt(built.stdout or built.stderr or "")}


def rebuild_revision(spec: ReproSpec, container: Container, revision: str,
                     python_version: str = "", era: str = "", pins=(),
                     recipe: str = "") -> dict:
    """Rebuild ONE revision's `.venv` the way the harness built it, on another CPython
    and/or with dependency pins narrowed.

    The environment is fixed before any agent runs, and an agent's own attempt to
    change it is refused as a reinstall over the prepared build (numba#10398 needed
    3.11, was built on 3.10, and all four of PEVR's rebuild commands were refused),
    or rejected by the era constraints file (its `pip install numpy==1.26.4` hit the
    pinned numpy==2.4.1) — and a numpy swapped under a COMPILED package breaks it
    anyway. This is the sanctioned route: the same build, into the same `.venv` that
    every command and test already resolves first, with era pins re-resolved for
    the interpreter and narrowed by `pins` (`numpy<2.4` -> the era's newest 2.3.x),
    and the test dependencies installed into it.
    """
    import dataclasses
    python_version = python_version or spec.python_version
    interpreter = ensure_interpreter(container, python_version)
    if not interpreter:
        return {"built": False, "python_version": python_version,
                "error": f"no CPython {python_version} could be found or installed"}
    respec = dataclasses.replace(spec, python_version=python_version)
    if recipe:
        # the agent's declaration replaces the harness's classification
        respec = dataclasses.replace(respec, needs_build=(recipe == "compiled"),
                                     language="python")
    workdir = FIXED_DIR if revision == "fixed" else BUGGY_DIR
    overrides = parse_pins(pins, python_package(respec))
    if recipe == "pure":
        result = _build_pure_venv(respec, container, revision, interpreter, era, overrides)
    elif respec.needs_build and (respec.language or "python") == "python":
        result = dict(build_revisions(respec, container, era=era, revisions=(revision,),
                                      interpreter=interpreter, overrides=overrides).get(revision) or {})
    else:
        package = python_package(respec)
        tag = "cp" + python_version.replace(".", "")
        base = era_pin_set(respec, container=container, workdir=workdir)
        declared = strategy_requirements(respec) if era_mode() == ERA_MODE_DYNAMIC else {}
        merged = {**declared, **overrides}      # an explicit override always wins
        pins = {d: v for d in base + tuple(o for o in merged if o not in base)
                if d != package.lower() and (era or d in merged)
                and (v := era_version(d, era or "9999", tag, specifier=merged.get(d, "")))}
        # the old pins were resolved for the old interpreter and may have no wheel for this one
        write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
                   "".join(f"{k}=={v}\n" for k, v in pins.items()))
        venv = _venv_home(workdir)
        made = container.run(f"rm -rf {venv} && {interpreter} -m venv {venv} && rm -rf .venv && "
                             f"ln -s {venv} .venv && .venv/bin/pip install --no-cache-dir -e .",
                             workdir=workdir, timeout_s=2400)
        result = {"built": made.exit_code == 0, "exit_code": made.exit_code,
                  "era_pins": pins, "output": _build_excerpt(made.stdout or made.stderr or "")}
    deps = [str(d).strip() for d in (respec.test_deps or ()) if str(d).strip()]
    if result.get("built") and deps:
        # a different interpreter does not see the image's site-packages, pytest included
        container.run(".venv/bin/pip install --no-cache-dir " + " ".join(shlex.quote(d) for d in deps),
                      workdir=workdir, timeout_s=1800)
    got = container.run(f"{_venv_home(workdir)}/bin/python -c "
                        f"'import platform; print(platform.python_version())'",
                        workdir="/", timeout_s=60)
    if recipe and python_package(respec).isidentifier():
        # A build the agent chose is checked, not trusted: it counts only if this
        # revision's interpreter imports this revision's own code.
        location = imports_from(container, workdir, python_package(respec))
        owned = owns_import(location, revision)
        result["imports_from"] = location
        result["owns_import"] = owned
        if result.get("built") and not owned:
            result["built"] = False
            result["error"] = (f"built, but {workdir}/.venv/bin/python imports "
                               f"{python_package(respec)} from {location or 'nowhere'} — "
                               f"not this revision's own source or venv")
    return result | {"python_version": python_version, "interpreter": interpreter,
                     "recipe": recipe, "pins_requested": overrides,
                     "venv_python": (got.stdout or "").strip()[-20:]}


def build_revisions(spec: ReproSpec, container: Container, rounds: int = 4,
                    era: str = "", revisions: tuple = ("buggy", "fixed"),
                    interpreter: str = "python", overrides: dict | None = None) -> dict:
    """Build a compiled instance ONCE PER REVISION, into that revision's own venv.

    scipy is not importable from a source checkout: its extension modules have
    to be compiled first, so every test and every probe dies at `import` and the
    revision is never exercised at all. Building is therefore preparation, not
    something a step-limited agent should be improvising.

    Each revision gets its OWN virtualenv because both hold a package of the
    same name: a shared site-packages would let whichever revision was built
    last answer for both, and the comparison would be between a revision and
    itself. `--system-site-packages` keeps the toolchain (meson, cython, numpy)
    shared and installs only the repository into the venv.

    `revisions` and `interpreter` let one revision be rebuilt on a different
    CPython after the fact (see `rebuild_revision`); the defaults are the
    up-front build of both revisions on the image's own interpreter.
    """
    if not spec.needs_build or (spec.language or "python") != "python":
        return {"skipped": "not a compiled Python instance"}
    package = python_package(spec)
    report = {}
    # A meson or setuptools-scm build reads the version out of git, so git has
    # to exist AND be willing to read a `docker cp`'d tree — the same thing the
    # pure-Python path ensures before it installs.
    if _vcs_versioned(container, BUGGY_DIR):
        report["git"] = _ensure_git(container).get("git")
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        if rev not in revisions:
            continue
        # The venv lives OUTSIDE the checkout, with `.venv` a symlink to it.
        # Inside, numpy's and pythran's include dirs are paths in the source
        # tree, and meson refuses them — "Tried to form an absolute path to a
        # dir in the source tree" (scikit-image's `include_directories`). The
        # build calls the venv by its real path so meson sees the outside one.
        venv = _venv_home(workdir)
        # removed first: a rebuild on another interpreter must not inherit the old venv
        made = container.run(f"rm -rf {venv} && {interpreter} -m venv --system-site-packages {venv} && "
                             f"rm -rf .venv && ln -s {venv} .venv",
                             workdir=workdir, timeout_s=600)
        if made.exit_code != 0:
            report[rev] = {"built": False,
                           "error": (made.stderr or "").strip()[-300:] or "venv failed"}
            continue
        # Pin the era BEFORE anything is built: an extension compiled against
        # numpy 2.5 and run against 2.1 is its own ABI failure. The pins go in a
        # constraints file that every later pip in this revision honours — the
        # test extras, the skip-driven installs, the agent's own commands — so
        # nothing quietly upgrades numpy back to today's.
        pins = {}
        overrides = overrides or {}
        if era or overrides:
            tag = "cp" + (spec.python_version or "").replace(".", "")
            own = (spec.repo.rsplit("/", 1)[-1] or "").lower()
            # an override narrows the era pick (numba#10398: "numpy<2.4" turns the
            # era's 2.4.1, which removed np.trapz, into the newest 2.3.x)
            base = era_pin_set(spec, container=container, workdir=workdir)
            declared = strategy_requirements(spec) if era_mode() == ERA_MODE_DYNAMIC else {}
            merged = {**declared, **overrides}   # an explicit override always wins
            for dependency in base + tuple(d for d in merged if d not in base):
                if dependency == own or (dependency not in merged and not era):
                    continue                     # never pin the thing under test
                version = era_version(dependency, era or "9999", tag,
                                      specifier=merged.get(dependency, ""))
                if version:
                    pins[dependency] = version
            if pins:
                write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
                           "".join(f"{k}=={v}\n" for k, v in pins.items()))
                # Installed into the venv explicitly, not left to a later
                # resolve: the image's own numpy is visible through
                # --system-site-packages and would otherwise answer for it.
                container.run(".venv/bin/pip install --no-cache-dir "
                              + " ".join(f"{shlex.quote(k)}=={shlex.quote(v)}"
                                         for k, v in pins.items()),
                              workdir=workdir, timeout_s=1800)
        # A build requirement the era already pinned is left to the pin: the
        # constraints file would reject both in one resolve (astropy asks for
        # numpy>=2 to build, the era says 1.26), failing the WHOLE install —
        # Cython included. Compiling against the pinned numpy is also the one
        # that matches what the revision will run against.
        # --no-build-isolation means the build backend must already be in the
        # venv, and a project that declares none (sympy has no [build-system]
        # table) still needs one: since Python 3.12 a venv ships pip alone, so
        # pip fails with "Cannot import 'setuptools.build_meta'" before the
        # build starts.
        declared = _build_requirements(container, workdir) or ["setuptools", "wheel"]
        # Only the requirements whose markers hold for THIS interpreter. Without
        # this, a later line for a different Python silently overrides the one
        # that applies — see _marker_applies.
        applicable = [r for r in declared
                      if _marker_applies(r, spec.python_version)]
        skipped = [r for r in declared if r not in applicable]
        if skipped:
            report.setdefault("build_requires_skipped", {})[rev] = skipped
        declared = applicable or ["setuptools", "wheel"]
        # An era pin must never violate what the project needs to BUILD. scipy's
        # 2024 sources use numpy 2's C API (npy_csetreal); pinned to the era's
        # numpy 1.26 they compiled with implicit declarations — a build that
        # "succeeded" and produced extensions that cannot load, so every test
        # module importing scipy.linalg was "not found".
        repinned = {}
        for requirement in declared:
            name = _requirement_name(requirement)
            # NOT `spec`: that name is this function's ReproSpec parameter, and
            # shadowing it turned spec.python_version into a string attribute error
            constraint = _requirement_spec(requirement)
            if name in pins and constraint and not _satisfies(pins[name], constraint):
                better = era_version(name, era, tag, specifier=constraint) if era else ""
                if better:
                    pins[name] = repinned[name] = better
        if repinned:
            write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
                       "".join(f"{k}=={v}\n" for k, v in pins.items()))
            container.run(".venv/bin/pip install --no-cache-dir "
                          + " ".join(f"{shlex.quote(k)}=={shlex.quote(v)}"
                                     for k, v in repinned.items()),
                          workdir=workdir, timeout_s=1800)
        requires = [r for r in declared if _requirement_name(r) not in pins]
        # The BUILD TOOLS are era-pinned too. `Cython>=3.2.0` installs today's
        # Cython, which generates C an older scipy cannot compile
        # ("cython_blas.c: invalid type argument of '->'"). The revision was
        # written against the Cython of its own week, so that is what it gets.
        if era and requires:
            tool_pins = {}
            for requirement in requires:
                name = _requirement_name(requirement)
                if not name or name in pins:
                    continue
                # WITH the requirement's own specifier: statsmodels asks for
                # `setuptools_scm>=9.2.0,<10` and the era's newest is 10.2.3 —
                # pinning that contradicts the requirement, pip resolves
                # nothing, and the build backend itself never installs
                # ("No module named 'mesonpy'").
                version = era_version(name, era, tag,
                                      specifier=_requirement_spec(requirement))
                if version:
                    tool_pins[name] = version
            if tool_pins:
                pins.update(tool_pins)
                write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
                           "".join(f"{k}=={v}\n" for k, v in pins.items()))
        if requires:
            container.run(".venv/bin/pip install --no-cache-dir "
                          + " ".join(shlex.quote(r) for r in requires),
                          workdir=workdir, timeout_s=1800)
        placed = _venv_owns_programs(container, workdir)
        # Each known failure has ONE known remedy, applied at most once and
        # recorded by name: a rebuild is then as reproducible as the first
        # build, which is what lets a packaged image rebuild the same way.
        supplied, remedies = [], {}          # remedy -> the env it adds
        dropped_pins = []                    # era pins the revision itself forbids
        if getattr(spec, "sanitizer", "") == "undefined":
            remedies["ubsan"] = _UBSAN       # from the first round: it IS the build
        for _ in range(rounds):
            env_prefix = _env_prefix(remedies)
            # The log is kept in the revision (packaging can carry it) and
            # reported as the FAILING block plus the tail: ninja prints the
            # compiler error when a target fails and then keeps printing OTHER
            # targets' progress, so the tail alone shows a hundred successful
            # compiles and no reason.
            built = container.run(
                env_prefix
                # The venv FIRST on PATH: a build backend runs helper scripts by
                # their `#!/usr/bin/env python3` shebang, and outside the venv
                # that is the image's interpreter, which cannot see the build
                # requirements pip just installed. statsmodels' meson.build runs
                # `statsmodels/_build/git_version.py` for the project version;
                # under the image's python its `import setuptools_scm` fails and
                # meson reports only "failed with status 1".
                + f'export PATH="{venv}/bin:$PATH"; '
                + f"{venv}/bin/pip install --no-build-isolation --no-cache-dir . "
                "> .numagent-build.log 2>&1; code=$?; "
                # meson keeps the REASON a sub-command failed in its own log and
                # prints only the exit status, so the failing log joins ours —
                # that is what lets the remedies below recognise the failure.
                "if [ $code -ne 0 ]; then for l in .mesonpy-*/meson-logs/meson-log.txt "
                "build/meson-logs/meson-log.txt; do [ -f \"$l\" ] && "
                "{ echo \"--- $l ---\"; tail -c 3000 \"$l\"; } >> .numagent-build.log; "
                "done; fi; "
                # The WHOLE build log, not an excerpt. A successful build matched
                # none of the error patterns the old grep looked for, so all that
                # survived was `tail -c 2500` — a window that opens mid-stream
                # ("Processing /workspace/buggy") and hides which compiler,
                # toolchain and pins actually ran. shell.py still caps a captured
                # stream at 200k chars, so a vast log is trimmed in the middle
                # rather than beheaded.
                "echo '--- full build log ---'; cat .numagent-build.log; "
                "exit $code",
                workdir=workdir, timeout_s=5400)
            if built.exit_code == 0:
                break
            log = built.stdout or ""
            if "scm_version" not in remedies and _NO_SCM_VERSION.search(log):
                remedies["scm_version"] = _PRETEND_VERSION
                continue
            if "permissive_c" not in remedies and _strict_c_error(container, workdir, log):
                remedies["permissive_c"] = _PERMISSIVE_C
                continue
            # An era pin the project itself forbids: numba's 2025 sources need
            # `llvmlite>=0.45.0dev0`, released months after the commit, so the
            # era's 0.44 made pip refuse the whole install. What the revision
            # DECLARES outranks the era — but left to pip, "the version that
            # satisfies it" is the NEWEST: llvmlite 0.45.1, whose initialize()
            # raises, so numba#10052 built and then failed at every import. The
            # pin is re-resolved inside the requirement instead, as close to the
            # era as it allows (here 0.45.0); only an unsatisfiable one is dropped.
            conflicting = [n for n in _PIN_CONFLICT.findall(log)
                           if n in pins and n not in dropped_pins]
            if conflicting:
                for name in conflicting:
                    pins.pop(name, None)
                    required = _required_specifier(log, name)
                    version = era_version(name, era, tag, specifier=required) if required and era else ""
                    if version:
                        pins[name] = version
                        repinned[name] = version
                dropped_pins += [n for n in conflicting if n not in pins]   # NOT in `remedies`: env
                write_file(container, f"{workdir}/{CONSTRAINTS_FILE}",
                           "".join(f"{k}=={v}\n" for k, v in pins.items()))
                continue
            missing = [m for m in dict.fromkeys(_MISSING_MODULE.findall(log))
                       if m not in supplied and m != package]
            if not missing:
                break
            installed = container.run(
                ".venv/bin/pip install --no-cache-dir "
                + " ".join(shlex.quote(m) for m in missing),
                workdir=workdir, timeout_s=900)
            if installed.exit_code != 0:
                break
            supplied += missing

        # The build "succeeded" only if the package now imports from the venv.
        # An import that resolves back into the source tree is the failure this
        # whole step exists to prevent, so the path is checked, not just the code.
        # The project's test plugins, declared by the project itself. Installed
        # after the build so a failure here cannot be mistaken for a build error.
        extras = _test_extra_requirements(container, workdir) if built.exit_code == 0 else []
        # the same filter as the mined dependencies: a GUI pytest plugin with no
        # toolkit behind it stops pytest from starting at all
        from .repro_specs import _NOT_NEEDED
        extras = [r for r in extras
                  if not _NOT_NEEDED.search(re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0])]
        # AFTER the filter, deliberately. `_NOT_NEEDED` drops the docs stack by
        # keyword, and "doctest" matches pytest-doctestplus while "mypy" matches
        # pytest-mypy-plugins — so astropy's own declared pytest-doctestplus was
        # filtered out and pytest then exited 4 on "unrecognized arguments:
        # --doctest-rst" before collecting a test. A plugin the project's
        # `addopts` NAMES is not optional tooling: without it pytest does not
        # start. Re-added for both revisions, since an install that reaches only
        # one turns a like-for-like comparison into "pass on buggy, error on
        # fixed" — which reads as the fix breaking the test.
        if built.exit_code == 0:
            for plugin in _addopts_plugins(container, workdir):
                if not any(_requirement_name(r) == plugin for r in extras):
                    extras.append(plugin)
        pyargs_plugin = install_pyargs_plugin(container, workdir) if built.exit_code == 0 else False
        extras = _era_plugins(extras, era, tag) if era else extras
        if extras:
            container.run(".venv/bin/pip install --no-cache-dir "
                          + " ".join(shlex.quote(r) for r in extras),
                          workdir=workdir, timeout_s=1800)
        # One at a time and never fatal: an import name need not be a pip name
        # (`yaml` is PyYAML), and one miss must not cost the others.
        skips = _importorskip_requirements(container, workdir, spec, package) if built.exit_code == 0 else []
        for module in skips:
            container.run(f".venv/bin/pip install --no-cache-dir {shlex.quote(module)} || true",
                          workdir=workdir, timeout_s=900)

        # Run from / with an ABSOLUTE interpreter path: at the revision root the
        # source tree would answer the import and prove nothing, and a relative
        # `.venv/bin/python` does not exist anywhere else.
        probe = (f'{workdir}/.venv/bin/python -c '
                 f'"import {package} as m; print(m.__file__)" 2>&1'
                 if package.isidentifier() else "")
        where = container.run(probe, workdir="/", timeout_s=300) if probe else None
        location = (where.stdout or "").strip() if where else ""
        report[rev] = {"built": built.exit_code == 0 and "/.venv/" in location,
                       "exit_code": built.exit_code, "imports_from": location,
                       "build_requires": requires, "installed_for_build": supplied,
                       "pretend_version": "scm_version" in remedies,
                       "remedies": list(remedies), "dropped_pins": dropped_pins,
                       "sanitizer": (_sanitizer_linked(container, workdir, package)
                                     if "ubsan" in remedies else None),
                       "era_pins": pins, "repinned_for_build": repinned,
                       "venv_programs": placed, "pyargs_plugin": pyargs_plugin,
                       "test_extras": extras[:12], "importorskip": skips,
                       "output": _build_excerpt(built.stdout or built.stderr or "")}
    return report


def _build_excerpt(output: str, limit: int = 8000) -> str:
    """What a reader needs from a build log, when the whole thing will not fit.

    The command already prints the failing blocks FIRST and the tail second, so
    a plain tail slice — which is what this was — could drop the compiler error
    and keep a hundred lines of successful progress. Long output keeps both
    ends, and says how much went missing in between.
    """
    text = (output or "").strip()
    if len(text) <= limit:
        return text
    head, tail = text[:limit * 5 // 8], text[-(limit * 3 // 8):]
    middle = text[len(head):len(text) - len(tail)]
    # A compile that fails at step 1309 of 1536 puts its diagnostic in the
    # MIDDLE: the head holds meson's configure output and the tail holds
    # "ninja: build stopped", so a head+tail excerpt keeps everything except the
    # one line that says why. Carry the first error block through as well.
    quoted = ""
    for i, line in enumerate(middle.splitlines()):
        if re.search(r"\berror:|^FAILED:|^\s+FAILED:", line):
            block = middle.splitlines()[max(0, i - 2):i + 12]
            quoted = ("\n\n… first error in the elided region …\n"
                      + "\n".join(block))
            break
    return (f"{head}\n\n… [{len(middle)} characters of build log elided; the full "
            f"log is at .numagent-build.log inside the revision] …{quoted}\n\n{tail}")


def _instance_crates(spec: ReproSpec) -> set:
    """The workspace members the instance's own patches touch.

    Removing one of these to make the workspace resolve would delete the thing
    being reproduced, so the repair refuses to and says so instead.
    """
    crates = set()
    for path in list(spec.source_files or ()) + test_files_of(spec):
        head = str(path).strip("/").split("/", 1)[0]
        if head and not head.endswith((".rs", ".toml")):
            crates.add(head)
    return crates


def repair_rust_workspaces(spec: ReproSpec, container: Container) -> dict:
    """Make both revisions resolve again, identically, or explain why they don't.

    Rust instances rot: a crate the workspace requires can be yanked from
    crates.io years after the merge, and Cargo resolves every member before it
    builds any of them, so one dead requirement in an unrelated crate blocks the
    whole instance. Moving that requirement to the nearest surviving version is
    the only repair that keeps the revisions comparable — which is why the SAME
    rewrites land on both trees, even when only one revision needed them.
    """
    keep = _instance_crates(spec)
    report = {"buggy": repair_cargo_workspace(container, BUGGY_DIR, keep=keep),
              "fixed": repair_cargo_workspace(container, FIXED_DIR, keep=keep)}
    if not any(r["applied"] for r in report.values()):
        return report
    union, seen = [], set()
    for rev in ("buggy", "fixed"):
        for edit in report[rev]["retargets"]:
            key = (edit["crate"], edit["manifest"])
            if key not in seen:
                seen.add(key)
                union.append(edit)
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        have = {(e["crate"], e["manifest"]) for e in report[rev]["retargets"]}
        extra = [e for e in union if (e["crate"], e["manifest"]) not in have]
        if extra and apply_retargets(container, workdir, extra):
            report[rev]["retargets"] += extra
            report[rev]["applied"] = True
            report[rev]["resolves"] = resolves(container, workdir)[0]
    report["retargets"] = union
    described = ", ".join(f"{e['crate']} {e['from']} -> {e['to']}" for e in union)
    spec.environment_notes = tuple(spec.environment_notes or ()) + (
        f"the Cargo workspace no longer resolved — {described} — because the pinned "
        f"version has been yanked from crates.io since the merge. The requirement was "
        f"moved in {', '.join(sorted({e['manifest'] for e in union}))} on BOTH "
        f"revisions. No crate the fix or the test touches was changed.",)
    return report


#: A lockfile means the project pinned its tree; `npm ci` reproduces it exactly
#: (and refuses to proceed if package.json disagrees). Without one, `npm install`
#: resolves. Scripts stay ON: some packages build native parts in postinstall.
_NODE_INSTALL = ("if [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then "
                 "npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi "
                 "> .numagent-npm.log 2>&1; code=$?; tail -c 1500 .numagent-npm.log; "
                 "exit $code")


#: what a JS test file imports says how it must be RUN. tape tests are plain
#: programs (`node test.js`); mocha's need its runner; node:test has its own.
_JS_RUNNERS = (
    ("tape", re.compile(r"""require\(\s*['"]tape['"]|from\s+['"]tape['"]""")),
    ("node-test", re.compile(r"""require\(\s*['"]node:test['"]|from\s+['"]node:test['"]""")),
    ("jest", re.compile(r"""@jest/globals|require\(\s*['"]jest['"]""")),
    ("ava", re.compile(r"""require\(\s*['"]ava['"]|from\s+['"]ava['"]""")),
)


def js_test_runner(container: Container, workdir: str, test_file: str) -> str:
    """Which runner this test file needs — "" when it looks like mocha's.

    stdlib's suites are tape programs: `npx mocha` on one reports no tests, and
    the file never runs. The file itself says which, in its imports.
    """
    head = container.run(f"head -c 4000 {shlex.quote(test_file)} 2>/dev/null",
                         workdir=workdir, timeout_s=60).stdout or ""
    for name, pattern in _JS_RUNNERS:
        if pattern.search(head):
            return name
    return ""


def install_node_dependencies(spec: ReproSpec, container: Container) -> dict:
    """Install each JavaScript/TypeScript revision's OWN dependencies.

    Python keeps test dependencies in the image (or in both venvs), so an
    install made while working in one revision serves the other. Node does not:
    `node_modules` lives INSIDE each checkout. An agent that runs `npm ci` in the
    buggy tree leaves the fixed tree with nothing — and every `npx mocha` there
    fails before a test runs, which reads as the FIX being broken. mathjs
    #3389, #3369, #3193 and #3136 were all graded SETUP_FAILURE for exactly
    that: `pass->error`, the test never having been attempted on fixed.

    So both are installed here, identically, before the agent starts.
    """
    if (spec.language or "") not in ("javascript", "typescript"):
        return {"skipped": "not a JavaScript/TypeScript instance"}
    # npm shells out to git for any dependency given as a git URL — stdlib's
    # test framework is one ("tape": "git+https://github.com/kgryte/tape.git") —
    # and the node images ship no git: the install dies with "spawn git ENOENT",
    # then every test fails with "Cannot find module 'tape'".
    report = {"git": _ensure_git(container).get("git")}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        present = container.run("test -f package.json && echo yes", workdir=workdir,
                                timeout_s=60)
        if "yes" not in (present.stdout or ""):
            report[rev] = {"installed": False, "reason": "no package.json"}
            continue
        # A fix rarely changes what a project DEPENDS on, and an install can
        # take twenty minutes (stdlib pulls four dependencies from git). When
        # both revisions declare exactly the same manifest, the second is a
        # copy of the first — same tree, a fraction of the time.
        if rev == "fixed" and (report.get("buggy") or {}).get("installed"):
            same = container.run(
                'b=/workspace/buggy; f=/workspace/fixed; '
                'if cmp -s "$b/package.json" "$f/package.json"; then '
                '  if [ -f "$b/package-lock.json" ] || [ -f "$f/package-lock.json" ]; then '
                '    cmp -s "$b/package-lock.json" "$f/package-lock.json" && echo same; '
                '  else echo same; fi; fi',
                workdir="/", timeout_s=120)
            if "same" in (same.stdout or ""):
                copied = container.run("cp -a /workspace/buggy/node_modules "
                                       "/workspace/fixed/node_modules", workdir="/",
                                       timeout_s=1800)
                if copied.exit_code == 0:
                    report[rev] = {"installed": True, "exit_code": 0,
                                   "copied_from": "buggy (identical manifest)"}
                    continue
        r = container.run(_NODE_INSTALL, workdir=workdir, timeout_s=1800)
        report[rev] = {"installed": r.exit_code == 0, "exit_code": r.exit_code,
                       "output": (r.stdout or r.stderr or "").strip()[-600:]}
    return report


def prepare_workspace(spec: ReproSpec, container: Container, *,
                      token: str | None = None, host_work_dir: Path,
                      defer_build: bool = False) -> dict:
    """Clone + check out both revisions and copy them into the container.

    Returns a status dict: which revisions were provisioned, whether the
    reproducing-test patch applied cleanly to the buggy tree, and which test
    dependencies were pre-installed.
    """
    host_work_dir = Path(host_work_dir)
    clone = host_work_dir / f"{spec.slug}_clone"
    info = {"buggy_dir": BUGGY_DIR, "fixed_dir": FIXED_DIR,
            "buggy_provisioned": False, "fixed_provisioned": False,
            "test_patch_applied": False, "test_deps": {}, "error": ""}
    try:
        # --- buggy: buggy_commit + reproducing test patch --------------------
        repo_mod.clone_and_reset(spec.repo, spec.buggy_commit, clone, token=token)
        tp = Path(spec.test_patch_path).resolve()  # git apply runs with cwd=clone
        if tp.exists() and tp.read_text(encoding="utf-8").strip():
            applied = apply_test_patch_with_blobs(clone, spec, tp)
            info["test_patch_applied"] = applied["applied"]
            if applied["error"]:
                info["test_patch_error"] = applied["error"]
            if applied["restored"] or applied["missing"]:
                info["test_files_restored"] = applied
        else:
            info["test_patch_error"] = f"empty/missing test patch: {tp}"
        container.copy_in(clone, BUGGY_DIR)
        info["buggy_provisioned"] = True
        if (spec.language or "python") == "python" and not getattr(spec, "import_name", ""):
            spec.import_name = discover_import_name(container, BUGGY_DIR, spec.repo)
            info["import_name"] = spec.import_name

        # --- fixed: fixed_commit as-is (already has test + fix) --------------
        info["buggy_commit_date"] = commit_date(clone, spec.buggy_commit)
        if spec.fixed_commit:
            # resetting the same clone to the fixed commit discards the applied
            # test patch and yields the merged (test + fix) tree.
            repo_mod.clone_and_reset(spec.repo, spec.fixed_commit, clone, token=token)
            info["fixed_commit_date"] = commit_date(clone, spec.fixed_commit)
            container.copy_in(clone, FIXED_DIR)
            info["fixed_provisioned"] = True
        else:
            info["error"] = "spec has no fixed_commit; only /workspace/buggy prepared"
        restored = (info.get("test_files_restored") or {}).get("restored") or []
        info["reproducing_test_present"] = bool(info["test_patch_applied"])
        if restored:
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                f"{len(restored)} binary test fixture(s) the patch could not carry were "
                f"taken from the fixed commit instead ({', '.join(restored[:4])}"
                + (" …" if len(restored) > 4 else "")
                + "). Every file with actual test code was patched normally, so the "
                "buggy revision's source is untouched.",)
        # The miner describes the project; the checkout IS the project. When the
        # tree says compiled and the spec says pure Python, the tree wins — and
        # the toolchain has to be installed before anything can act on that.
        if (spec.language or "python") == "python" and not spec.needs_build:
            evidence = detect_compiled_layout(container, BUGGY_DIR)
            if evidence:
                spec.needs_build = True
                info["build_detected"] = evidence
        info["system_packages"] = install_system_packages(spec, container)
        info["test_deps"] = install_test_deps(spec, container)
        # `docker cp` preserves the host's ownership, so git inside the container
        # sees a repository owned by someone else and refuses to read it. Any
        # tooling that asks git a question then fails — setuptools-scm cannot
        # find a version, and the build dies before it compiles anything.
        container.run("git config --global --add safe.directory '*'",
                      workdir="/", timeout_s=60)
        # the LATER of the two dates: the fixed revision must import too, and
        # a fix is merged days, not years, after the code it corrects
        info["era"] = info.get("fixed_commit_date") or info.get("buggy_commit_date") or ""
        # which policy chose the pinned set, and what it chose: a result that
        # does not say this cannot be compared against one run the other way
        info["era_mode"] = era_mode()
        info["era_pin_set"] = list(era_pin_set(spec, container=container))
        # build-in-loop ablation: skip the deterministic build so the agent's
        # first plan builds both revisions from the strategy, then runs the test.
        info["build"] = ({"skipped": "deferred to the agent (build-in-loop)",
                          "deferred": True} if defer_build
                         else build_revisions(spec, container, era=info["era"]))
        if getattr(spec, "sanitizer", "") == "undefined":
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "both revisions are BUILT WITH UBSan (-fsanitize=undefined): this bug is "
                "undefined behaviour in compiled code, which on x86 usually gives the "
                "right answer anyway. A line `<file>:<line>: runtime error: …` on stderr, "
                "in the code the fix changes, IS the defect. Run pytest with -s, or it "
                "hides a passing test's stderr — and the report with it.",)
        info["runtime"] = provision_pure_python(spec, container, era=info["era"])
        runtime = info["runtime"]
        if runtime.get("ok"):
            comp = runtime.get("companion") or {}
            pinned = [f"{comp['wheel']}=={comp.get('fallback') or comp['wanted']}"] \
                if comp.get("installed") else []
            pinned += [f"{k}=={v}" for k, v in (runtime.get("era_pins") or {}).items()]
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "the runtime is ALREADY installed for both revisions, at the versions "
                "they were written against: " + ", ".join(pinned) + ". Each revision's "
                "source tree is what `import` finds when you run from its directory. Do "
                "NOT reinstall or upgrade these (a plain `pip install jaxlib` fetches one "
                "newer than this jax accepts); the pins in .numagent-constraints.txt "
                "apply to any pip run from a revision directory.",)
        info["test_requirements"] = install_test_requirements(container)
        if info.get("build_detected"):
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                f"this checkout compiles extension modules ({info['build_detected']}) even "
                f"though the mined environment called it plain Python, so it was built "
                f"like any other compiled instance.",)
        built = [rev for rev, r in info["build"].items()
                 if isinstance(r, dict) and r.get("built")]
        if built:
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "this package has compiled extensions and cannot be imported from a "
                "source checkout, so each revision was BUILT into its own virtualenv "
                "at <revision>/.venv. Running anything with that revision as the "
                "working directory picks it up automatically (it is first on PATH; "
                "add `-I` so the un-built source tree stays off sys.path) — "
                "so `python`, `pip` and `pytest` already mean this revision's build. "
                "Import the package by name; do not add the source directory to "
                "sys.path. Run its tests by module from that build, never by source "
                "path: `python -I -m pytest --pyargs <pkg.tests.module> -k <case> "
                "--confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning`. "
                "-I keeps the working directory off sys.path (PYTHONSAFEPATH does not "
                "exist before Python 3.11), --confcutdir stops a root conftest.py "
                "importing the un-built tree, and -W stops today's pytest deprecating "
                "an older test file into a collection error. If the tests live "
                "OUTSIDE the package (a top-level tests/ directory, as in jax), there is "
                "no module to name — run the file instead: `python -I -m pytest "
                "tests/<file>.py -k <case> -W ignore::pytest.PytestDeprecationWarning`. "
                "Do NOT use "
                "`-p no:warnings`: it disables the project's own warning filters, so "
                "your run and the verdict's would test different things. "
                "The build is DONE — never run `setup.py build_ext`, `pip install -e .` "
                "or `pip install .` in a revision: an in-place or editable install "
                "takes over from the venv's copy, the package then imports from the "
                "un-built source tree, and every test and probe fails on that revision "
                "alone. Those commands are refused.",)
        info["cargo_workspace"] = repair_rust_workspaces(spec, container)
        info["node_dependencies"] = install_node_dependencies(spec, container)
        info["cmake"] = configure_cmake_project(spec, container)
        if info["cmake"].get("configured"):
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "cmake is installed and BOTH revisions are ALREADY configured, each in "
                "its own <revision>/.numagent-cmake"
                + (f" (installed for it: {', '.join(info['cmake']['installed'])})"
                   if info["cmake"].get("installed") else "")
                + ". Build from there (`cmake --build .numagent-cmake --target <t>`) "
                "rather than configuring again.",)
        elif (spec.language or "") in ("c", "cpp"):
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "run the reproducing test the SAME way on both revisions, each from "
                "its OWN tree: a build directory made under /workspace/buggy does not "
                "exist under /workspace/fixed, and a command that hardcodes one path "
                "grades the fix as broken rather than fixed.",)
        if (info["cmake"].get("configured")
                and any(str(f).endswith(".py") for f in test_files_of(spec))):
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "the reproducing test is a PYTHON test of this project's bindings: it "
                "needs the bindings BUILT (cmake --build .numagent-cmake, the binding "
                "target) and importable (PYTHONPATH to the build's python output), on "
                "each revision separately. ctest will not run it.",)
        if info["cmake"].get("unmapped") and not info["cmake"].get("configured"):
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "cmake is installed, but configuring needs packages this harness could "
                f"not name: {', '.join(info['cmake']['unmapped'][:6])}. Install them "
                "(apt-get) before building.",)
        if all((info["node_dependencies"].get(rev) or {}).get("installed")
               for rev in ("buggy", "fixed")):
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                "node_modules is already installed in BOTH revisions (npm ci against "
                "each tree's own lockfile). Do not reinstall; run the tests directly. "
                "If you do install something, install it in both trees — each "
                "revision has its own node_modules, and a package added to one is "
                "missing from the other.",)
        # which runner the suite needs, said once: `npx mocha` on a tape suite
        # collects nothing, and the file never runs
        tests = test_files_of(spec)
        runner = js_test_runner(container, BUGGY_DIR, tests[0]) if tests else ""
        if runner:
            how = {"tape": "`node <test file>` — these are tape programs, which run "
                           "themselves; mocha collects no tests from them",
                   "node-test": "`node --test <test file>`",
                   "jest": "`npx jest <test file>`",
                   "ava": "`npx ava <test file>`"}[runner]
            spec.environment_notes = tuple(spec.environment_notes or ()) + (
                f"this repository's tests run with {runner}: {how}.",)
        return info
    except Exception as e:  # noqa: BLE001 — surface prep failure to the caller
        info["error"] = f"{type(e).__name__}: {e}"[:300]
        return info
    finally:
        if clone.exists():
            shutil.rmtree(clone, ignore_errors=True)

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from numagent.repro_workspace import BUGGY_DIR, FIXED_DIR

BENCH = "/benchmark"
CANDIDATE = f"{BENCH}/candidate/test.py"

HANG_TIMEOUT_S = 120


@dataclass
class PackageOptions:
    enabled: bool = False
    prefix: str = "numbugbench/"
    tag: str = "latest"
    artifacts_dir: str = ""
    statuses: tuple = ("F2P_REPRODUCED", "REPRODUCED")


def image_name(spec, opts: PackageOptions) -> str:
    project = str(spec.repo).split("/")[-1].lower()
    pr = str(spec.slug).rsplit("_", 1)[-1]
    return f"{opts.prefix}{project}-{pr}:{opts.tag}"


_PROBES = {
    "python_version": "python -VV 2>&1 | head -2",
    "pip_freeze": "python -m pip freeze 2>/dev/null",
    "interpreter": "python -c 'import sys; print(sys.executable)' 2>/dev/null",
    "numpy": "python -c 'import numpy; print(numpy.__version__)' 2>/dev/null",
    "scipy": "python -c 'import scipy; print(scipy.__version__)' 2>/dev/null",
    "blas": ("python -c \"import numpy,json;"
             "print(json.dumps(getattr(numpy,'show_config',lambda **k:{})(mode='dicts')"
             ".get('Build Dependencies',{}),default=str))\" 2>/dev/null"),
}
_HOST_PROBES = {
    "system_packages": "dpkg-query -W -f='${Package}=${Version}\\n' 2>/dev/null | sort",
    "compiler": "(gcc --version 2>/dev/null; gfortran --version 2>/dev/null) | head -4",
    "arch": "uname -m; uname -r",
    "libc": "ldd --version 2>&1 | head -1",
}
_NUMERIC_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "PYTHONHASHSEED", "JAX_ENABLE_X64",
                "JAX_PLATFORM_NAME", "NPY_DISABLE_CPU_FEATURES", "PIP_CONSTRAINT")


def _ask(ctx, command: str, workdir: str) -> str:
    try:
        res = ctx.container.run(command, workdir=workdir, timeout_s=180)
    except Exception:
        return ""
    return (getattr(res, "stdout", "") or "").strip()


def freeze_environment(ctx) -> dict:
    env: dict = {"revisions": {}, "image": {}, "numeric_env": {}}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        answers = {k: _ask(ctx, cmd, workdir) for k, cmd in _PROBES.items()}
        blas = answers.pop("blas", "")
        try:
            answers["blas"] = json.loads(blas) if blas else {}
        except ValueError:
            answers["blas"] = {"raw": blas[:400]}
        env["revisions"][rev] = answers
    for key, cmd in _HOST_PROBES.items():
        env["image"][key] = _ask(ctx, cmd, BUGGY_DIR)
    shown = _ask(ctx, "env | sort", BUGGY_DIR)
    for line in shown.splitlines():
        name, _, value = line.partition("=")
        if name in _NUMERIC_ENV:
            env["numeric_env"][name] = value
    return env


_PRELUDE = '''#!/usr/bin/env bash
# Each revision owns its interpreter. Putting its .venv first is what makes
# `python` mean THIS revision's build rather than the other one's.
set -uo pipefail
# A hang instance's contract is not FAIL/PASS but "does not terminate within N
# seconds / PASS". N is declared in scripts/hang_timeout; absent or 0 means the
# ordinary contract. Both revisions get the same wrapper, so the two sides stay
# byte-for-byte comparable.
HANG_TIMEOUT="$(cat "$(dirname "$0")/hang_timeout" 2>/dev/null || echo 0)"
case "$HANG_TIMEOUT" in ''|*[!0-9]*) HANG_TIMEOUT=0 ;; esac
rev_dir() { [ "$1" = fixed ] && echo "%FIXED%" || echo "%BUGGY%"; }
enter() {
  cd "$(rev_dir "$1")" || exit 90
  if [ -x "$PWD/.venv/bin/python" ]; then PATH="$PWD/.venv/bin:$PATH"; export PATH; fi
  if [ -f "$PWD/.numagent-constraints.txt" ]; then
    PIP_CONSTRAINT="$PWD/.numagent-constraints.txt"; export PIP_CONSTRAINT; fi
}
'''

_CLASSIFY = '''
classify() {   # $1 = exit code, $2 = output file
  local code="$1" out="$2"
  if grep -qiE "no tests ran|collected 0 items$|ERROR collecting|ImportError while loading conftest|ModuleNotFoundError" "$out"; then
    echo ERROR; return
  fi
  case "$code" in
    0) echo PASS ;;
    1) echo FAIL ;;
    5) echo ERROR ;;      # nothing collected
    124|137)
      # `timeout` expiry. For a declared hang this IS the reported defect
      # (scipy#19944's bdtrik never leaves cdflib's root finder); anywhere else
      # it is a run that never reached a verdict.
      if [ "${HANG_TIMEOUT:-0}" -gt 0 ]; then echo FAIL; else echo ERROR; fi ;;
    *) echo ERROR ;;      # usage error, internal error, signal
  esac
}
'''

_RUN_ONE = '''
run_rev() {    # $1 = buggy|fixed
  enter "$1"
  local out; out="$(mktemp)"
  # `-rA` asks pytest for a summary naming EVERY case and its outcome, instead
  # of a row of dots. It has to go on the command LINE: PYTEST_ADDOPTS is
  # inserted before the command's own arguments, so a stored command carrying
  # `-rs` (scipy's do) wins and reports skips only. `-r` selects what the
  # summary lists — it changes neither what runs nor the exit code — so the
  # graded behaviour is still what this instance was validated with, and
  # `test_command_<rev>` keeps that command verbatim as the record. A compound
  # command (the --pyargs/path fallback ending in `fi`) is left alone: a
  # trailing `-rA` would land after `fi` and be a bash syntax error.
  local cmd; cmd="$(test_command "$1")"
  case "$cmd" in
    *pytest*) case "$cmd" in *" -rA"*|*"; fi") ;; *) cmd="$cmd -rA" ;; esac ;;
  esac
  if [ "${HANG_TIMEOUT:-0}" -gt 0 ]; then
    # bash -c, not eval: the command may begin with env assignments
    # (`MPMATH_MAX_PREC=200000 python …`) which `timeout` cannot exec directly
    timeout -k 5 "$HANG_TIMEOUT" bash -c "$cmd" >"$out" 2>&1
  else
    # in a subshell: the --pyargs/path fallback command ends in `exit`, which
    # under a bare eval would end this script instead of the test run
    # shellcheck disable=SC2086
    ( eval "$cmd" ) >"$out" 2>&1
  fi
  local code=$?
  cat "$out"
  local verdict; verdict="$(classify "$code" "$out")"
  rm -f "$out"
  echo "---"
  echo "revision: $1"
  echo "exit_code: $code"
  echo "result: $verdict"
  [ "$verdict" = ERROR ] && return 90
  [ "$verdict" = FAIL ] && return 1
  return 0
}
'''


def _runner_scripts(buggy_cmd: str, fixed_cmd: str, selector: str,
                    hang_timeout: int = 0) -> dict:
    head = _PRELUDE.replace("%BUGGY%", BUGGY_DIR).replace("%FIXED%", FIXED_DIR)
    chooser = (
        'test_command() {\n'
        f'  cat "{BENCH}/scripts/test_command_$1"\n'
        '}\n'
    )
    common = head + chooser + _CLASSIFY + _RUN_ONE

    run_buggy = common + '\nrun_rev buggy\n'
    run_fixed = common + '\nrun_rev fixed\n'

    validate = common + '''
# The test output is the evidence, so validate SHOWS it: which assertion changed
# state is the whole content of a numerical reproduction, and a verdict with the
# log withheld cannot be checked by whoever reads it.
# NUMBUGBENCH_QUIET=1 prints the verdict alone; NUMBUGBENCH_TAIL=N keeps only
# the last N lines of each side.
show() {   # $1 = file holding one revision's run
  if [ "${NUMBUGBENCH_QUIET:-0}" = 1 ]; then return; fi
  echo "    --- test output ---"
  awk -v tail="${NUMBUGBENCH_TAIL:-0}" '
    /^---$/ && !cut { cut = NR }
    { line[NR] = $0 }
    END {
      last = (cut ? cut - 1 : NR)
      first = 1
      if (tail > 0 && last - tail + 1 > 1) {
        first = last - tail + 1
        print "    | … " (first - 1) " earlier line(s) elided"
      }
      for (i = first; i <= last; i++) print "    | " line[i]
    }' "$1"
}
echo "NumBugBench: validating the reproduction"
echo
b_out="$(mktemp)"; f_out="$(mktemp)"
run_rev buggy >"$b_out" 2>&1; b_code=$?
run_rev fixed >"$f_out" 2>&1; f_code=$?
b=$(grep '^result: ' "$b_out" | tail -1 | cut -d' ' -f2)
f=$(grep '^result: ' "$f_out" | tail -1 | cut -d' ' -f2)
bc=$(grep '^exit_code: ' "$b_out" | tail -1 | cut -d' ' -f2)
fc=$(grep '^exit_code: ' "$f_out" | tail -1 | cut -d' ' -f2)
# Which cases actually ran, read off pytest's own summary rather than guessed:
# one line per case, with its outcome.
cases() {   # $1 = file holding the run, $2 = buggy|fixed
  local found
  # pytest's own per-case summary, when the command's -r flag allowed one.
  # PYTEST_ADDOPTS is inserted BEFORE the command line, so a command carrying
  # `-rs` (scipy's do) overrides the `-rA` we asked for and reports skips only.
  found=$(grep -hoE "^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) [^ ]+" "$1" | sort -u)
  if [ -z "$found" ]; then
    found=$(grep -hoE "^[^ ]+::[A-Za-z_][A-Za-z0-9_]*" "$1" | sort -u)
  fi
  if [ -z "$found" ] && [ -n "${2:-}" ]; then
    # Last resort: ask the SAME selection which cases it selects. A separate
    # --collect-only pass (no extra -q: `-q -q` collapses the listing into a
    # per-file count), so the graded run stays untouched — names without
    # outcomes beats no names.
    enter "$2"
    found=$(eval "$(test_command "$2") --collect-only" 2>/dev/null \
            | grep -E "::" | sort -u | head -60)
    [ -n "$found" ] && echo "    Cases (selected; outcomes above):" && {
      printf '%s\n' "$found" | while IFS= read -r line; do echo "      $line"; done
      return
    }
  fi
  if [ -z "$found" ]; then
    echo "    Cases: (the runner printed no per-case names)"
    return
  fi
  echo "    Cases:"
  printf '%s\n' "$found" | while IFS= read -r line; do echo "      $line"; done
}
echo "Buggy revision:"
echo "    Command: $(test_command buggy)"
echo "    Test execution: $([ "$b" = ERROR ] && echo ERROR || echo SUCCESS)"
echo "    Test result: $b (exit $bc)"
cases "$b_out" buggy
show "$b_out"
echo
echo "Fixed revision:"
echo "    Command: $(test_command fixed)"
echo "    Test execution: $([ "$f" = ERROR ] && echo ERROR || echo SUCCESS)"
echo "    Test result: $f (exit $fc)"
cases "$f_out" fixed
show "$f_out"
echo
echo "Reproduction:"
if [ "$b" = FAIL ] && [ "$f" = PASS ]; then
  echo "    VALID F2P"; rc=0
elif [ "$b" = ERROR ] || [ "$f" = ERROR ]; then
  echo "    INVALID (the test did not run on one revision)"; rc=2
else
  echo "    INVALID ($b -> $f)"; rc=3
fi
rm -f "$b_out" "$f_out"
exit $rc
'''

    evaluate = head + _CLASSIFY + f'''
CAND="${{1:-{CANDIDATE}}}"
if [ ! -f "$CAND" ]; then
  echo "{{\\"error\\": \\"no candidate test at $CAND — mount one\\"}}"; exit 64
fi
judge() {{      # $1 = buggy|fixed
  # `enter` is for the ENVIRONMENT (this revision's venv on PATH), not for the
  # working directory. Running pytest inside the revision put the SOURCE tree
  # first on sys.path, so `import numpy` resolved to unbuilt sources and both
  # sides returned ERROR — numpy#29500 and #29522 were classified INVALID for
  # that reason alone. The candidate therefore runs from a neutral directory,
  # where the import resolves to the built package in .venv.
  enter "$1"
  local work="/tmp/numbugbench-candidate-$1"
  rm -rf "$work"; mkdir -p "$work"
  cp "$CAND" "$work/test_candidate.py"
  local out; out="$(mktemp)"
  ( cd "$work" && python -m pytest test_candidate.py -q -rs -p no:randomly ) \
      >"$out" 2>&1
  # A neutral directory is right only when the subject is INSTALLED in this
  # revision's venv. Most packaged images do not install it: reproduction
  # passed because pytest ran INSIDE the checkout, where an in-place build
  # (compiled .so files in the tree) or a pure-Python tree satisfies the
  # import. For those images /tmp makes the subject importable nowhere and
  # every candidate scores ERROR on both sides — jax#20885 and #38820 did
  # exactly that for four iterations each. So an import failure, and only an
  # import failure, is retried with the checkout on PYTHONPATH, where it
  # shadows nothing because nothing is installed to shadow. A candidate whose
  # own import path is simply wrong fails both ways and keeps its verdict.
  if grep -qE "ModuleNotFoundError|ImportError" "$out" 2>/dev/null; then
    ( cd "$work" && PYTHONPATH="$(rev_dir "$1")" python -m pytest \
        test_candidate.py -q -rs -p no:randomly ) >"$out" 2>&1
  fi
  local code=$?
  classify "$code" "$out"
  if [ "${{NUMBUGBENCH_SHOW_CANDIDATE:-0}}" = 1 ]; then
    echo "--- $1 ---" >&2; cat "$out" >&2
  fi
  rm -rf "$out" "$work"
}}
B="$(judge buggy)"; F="$(judge fixed)"
if [ "$B" = FAIL ] && [ "$F" = PASS ]; then CLASS=F2P
elif [ "$B" = PASS ] && [ "$F" = PASS ]; then CLASS=P2P
elif [ "$B" = FAIL ] && [ "$F" = FAIL ]; then CLASS=F2F
elif [ "$B" = PASS ] && [ "$F" = FAIL ]; then CLASS=P2F
else CLASS=INVALID; fi
printf '{{"buggy": "%s", "fixed": "%s", "classification": "%s"}}\\n' "$B" "$F" "$CLASS"
[ "$CLASS" = F2P ] || exit 1
'''

    selector_q = shlex.quote(f"  the developer test for this bug is: {selector}")
    entrypoint = f'''#!/usr/bin/env bash
set -uo pipefail
case "${{1:-validate}}" in
  buggy)                   shift; exec {BENCH}/scripts/run_buggy.sh "$@" ;;
  fixed)                   shift; exec {BENCH}/scripts/run_fixed.sh "$@" ;;
  validate)                shift; exec {BENCH}/scripts/validate.sh "$@" ;;
  evaluate-generated-test) shift; exec {BENCH}/scripts/evaluate_generated_test.sh "$@" ;;
  metadata)                exec cat {BENCH}/metadata.json ;;
  shell)                   shift; exec bash "$@" ;;
  "")
    echo "usage: docker run <image> [buggy|fixed|validate|evaluate-generated-test|metadata|shell]" >&2
    # The selector is arbitrary shell text. Interpolated into a double-quoted
    # echo, numpy#29372's `python -c "import numpy, sys; …"` closed the string
    # and bash refused the whole file — breaking every entrypoint command for
    # that image, not just the usage message. printf with a single-quoted
    # argument cannot be terminated from inside.
    printf '%s\n' {selector_q} >&2
    exit 64 ;;
  *)
    # Anything that is not one of the verbs above is run as an ordinary command.
    # Without this, an ENTRYPOINT swallows `docker run <image> sleep infinity` —
    # which is how a harness keeps a container alive — so the container exited
    # immediately and every later `docker exec` failed. A benchmark image should
    # still behave like an image.
    exec "$@" ;;
esac
'''
    return {"run_buggy.sh": run_buggy, "run_fixed.sh": run_fixed,
            "validate.sh": validate,
            "evaluate_generated_test.sh": evaluate, "entrypoint.sh": entrypoint,
            "test_command_buggy": buggy_cmd + "\n",
            "test_command_fixed": fixed_cmd + "\n",
            "hang_timeout": f"{int(hang_timeout)}\n"}


def build_metadata(ctx, context, verdict, env: dict, image: str,
                   task: str, grading: str, hang_timeout: int = 0) -> dict:
    spec = ctx.spec
    buggy = _test_record(ctx, "buggy") or {}
    fixed = _test_record(ctx, "fixed") or {}
    report = ctx.report or {}
    pevr = report.get("numeric_pevr") or {}
    return {
        "benchmark": "NumBugBench",
        "instance_id": f"{str(spec.repo).split('/')[-1].lower()}-"
                       f"{str(spec.slug).rsplit('_', 1)[-1]}",
        "image": image,
        "project": spec.repo,
        "reference": getattr(context, "reference", ""),
        "category": getattr(context, "category", "") or spec.bug_type,
        "packaged": date.today().isoformat(),
        "buggy_revision": spec.buggy_commit,
        "fixed_revision": spec.fixed_commit,
        "historical_date": getattr(context, "pr_date", ""),
        "packaging_mode": "single-image-two-revisions",
        "revisions": {"buggy": BUGGY_DIR, "fixed": FIXED_DIR},
        "environment": {
            "frozen_in_image": True,
            "lockfiles": {"buggy": "env/buggy/requirements.lock",
                          "fixed": "env/fixed/requirements.lock",
                          "system": "env/system-packages.txt"},
            "python": (env["revisions"].get("buggy", {}).get("python_version") or "").split("\n")[0],
            "numpy": env["revisions"].get("buggy", {}).get("numpy"),
            "scipy": env["revisions"].get("buggy", {}).get("scipy"),
            "blas": env["revisions"].get("buggy", {}).get("blas"),
            "compiler": env["image"].get("compiler"),
            "arch": env["image"].get("arch"),
            "libc": env["image"].get("libc"),
            "numeric_env": env.get("numeric_env", {}),
        },
        "test": {
            "framework": "pytest",
            "files": sorted({f for f, _ in _pairs(spec)}),
            "cases": sorted({c for _, c in _pairs(spec)}),
            "command_buggy": buggy.get("command", ""),
            "command_fixed": fixed.get("command", ""),
            "selector": spec.test_selector,
            "expected_buggy": "TIMEOUT" if hang_timeout else "FAIL",
            "expected_fixed": "PASS",
            "timeout_s": hang_timeout or None,
            "source": "developer regression test, from the PR's test patch",
        },
        "failure_oracle": {
            "type": (verdict.buggy.failure_type if verdict else "") or "unknown",
            "expected_transition": "F2P",
            "match": "failure type and transition, not stdout",
            **({"manifestation": "hang",
                "timeout_s": hang_timeout,
                "expected_buggy": "does not terminate within timeout_s",
                "note": "the buggy revision is expected to EXPIRE; validate runs "
                        "both revisions under `timeout` and reads expiry on the "
                        "buggy side as the defect"}
               if hang_timeout else {}),
        },
        "reproduction": {
            "status": report.get("status"),
            "transition": report.get("transition"),
            "validated": True,
            "task": task,
            "grading": grading,
            "iterations": pevr.get("iterations"),
            "replans": pevr.get("replans"),
            "tokens_by_iteration": pevr.get("tokens_by_iteration") or [],
            "evidence_calls": pevr.get("evidence_calls"),
            "refusals": pevr.get("refusals"),
            "tokens": pevr.get("tokens"),
            "agent": "NumericPEVR",
        },
        **({"provisioning_warning": (ctx.report or {}).get("provisioning_warning")}
           if (ctx.report or {}).get("provisioning_warning") else {}),
        "contains_ground_truth": True,
        "leakage_note": (
            "This image is the EVALUATOR artifact: it holds the fixed revision "
            "and the developer test. An agent under evaluation must be given a "
            "workspace exposing only the buggy revision and the permitted "
            "context — never this image's /benchmark/fixed, test patch or "
            "metadata."),
        "entrypoints": {
            "buggy": "docker run <image> buggy",
            "fixed": "docker run <image> fixed",
            "validate": "docker run <image> validate",
            "evaluate_generated_test":
                f"docker run -v ./test.py:{CANDIDATE} <image> evaluate-generated-test",
        },
    }


def _pairs(spec) -> list:
    try:
        from numagent.repro_specs import test_case_pairs
        return test_case_pairs(spec) or []
    except Exception:
        return []


def _test_record(ctx, revision: str) -> dict | None:
    return (ctx.results.get(revision) or {}).get("test")


_DOCKERFILE = '''# {instance_id} — NumBugBench instance, rebuild recipe
#
# TRANSPARENCY ARTIFACT, NOT THE BUILD THAT PRODUCED THE IMAGE.
#
# The published image is a snapshot of the container in which the reproduction
# was validated, so its environment is the one that actually produced
#     T_dev(buggy) = FAIL   and   T_dev(fixed) = PASS.
# Re-running the steps below resolves dependencies again and may therefore land
# on a DIFFERENT environment — which is the drift this packaging exists to
# avoid. Use this file to understand or modify the instance; use the image to
# reproduce it.
#
#   docker run {image} validate
#
FROM {base_image}

# both revisions, as the reproduction left them
#   {buggy_dir}  {buggy_commit}   (+ the developer test patch applied)
#   {fixed_dir}  {fixed_commit}   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   {python}
#   numpy    {numpy}
#   scipy    {scipy}
#   arch     {arch}
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ {bench}/scripts/
COPY metadata.json {bench}/metadata.json
RUN mkdir -p {bench}/candidate && chmod +x {bench}/scripts/*.sh

ENTRYPOINT ["{bench}/scripts/entrypoint.sh"]
CMD ["validate"]
'''

_README = '''# {instance_id}

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run {image} validate   # checks FAIL on buggy, PASS on fixed
    docker run {image} buggy      # the developer test on the buggy revision
    docker run {image} fixed      # the same test on the fixed revision
    docker run {image} metadata   # the manifest

Bug: {reference}
Category: {category}
Buggy: {buggy_commit}
Fixed: {fixed_commit}

Test: {selector}

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:{candidate} {image} evaluate-generated-test

prints `{{"buggy": …, "fixed": …, "classification": …}}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.
'''


@dataclass
class PackageResult:
    packaged: bool = False
    image: str = ""
    artifacts: str = ""
    reason: str = ""
    metadata: dict = field(default_factory=dict)
    size_bytes: int = 0
    image_deleted: bool = False

    def as_dict(self) -> dict:
        return {"packaged": self.packaged, "image": self.image,
                "artifacts": self.artifacts, "reason": self.reason,
                "image_size_bytes": self.size_bytes,
                "image_size_gb": round(self.size_bytes / 1e9, 3) if self.size_bytes else None,
                "image_deleted": self.image_deleted}


def delete_image(image: str) -> bool:
    import subprocess
    try:
        out = subprocess.run(["docker", "rmi", "-f", image],
                             capture_output=True, text=True, timeout=120)
        return out.returncode == 0
    except Exception:
        return False


def image_size(image: str) -> int:
    import subprocess
    try:
        out = subprocess.run(["docker", "image", "inspect", "-f", "{{.Size}}", image],
                             capture_output=True, text=True, timeout=60)
        return int(out.stdout.strip()) if out.returncode == 0 else 0
    except Exception:
        return 0


def _stage(tmp: Path, meta: dict, env: dict, scripts: dict, spec,
           image: str, base_image: str) -> None:
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp / "candidate").mkdir(parents=True, exist_ok=True)
    (tmp / "tests").mkdir(parents=True, exist_ok=True)
    for rev in ("buggy", "fixed"):
        d = tmp / "env" / rev
        d.mkdir(parents=True, exist_ok=True)
        freeze = env["revisions"].get(rev, {}).get("pip_freeze", "")
        (d / "requirements.lock").write_text(freeze + "\n", encoding="utf-8")
        (d / "pip-freeze.txt").write_text(freeze + "\n", encoding="utf-8")
        (d / "interpreter.txt").write_text(
            (env["revisions"].get(rev, {}).get("interpreter", "") or "") + "\n",
            encoding="utf-8")
    (tmp / "env" / "system-packages.txt").write_text(
        (env["image"].get("system_packages", "") or "") + "\n", encoding="utf-8")
    (tmp / "env" / "numerical.json").write_text(
        json.dumps({"image": env["image"], "numeric_env": env.get("numeric_env", {}),
                    "per_revision": {r: {k: v for k, v in d.items()
                                         if k != "pip_freeze"}
                                     for r, d in env["revisions"].items()}},
                   indent=2, default=str), encoding="utf-8")

    patch = Path(getattr(spec, "test_patch_path", "") or "")
    if patch.is_file():
        (tmp / "tests" / "developer_test.patch").write_text(
            patch.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    (tmp / "tests" / "README.md").write_text(
        "The developer regression test for this bug, as the PR's test patch.\n"
        "It is ALREADY APPLIED in both revisions; this copy is the record of\n"
        "what was applied. metadata.json names the files, the cases and the\n"
        "exact command run on each revision.\n", encoding="utf-8")
    (tmp / "candidate" / "README.md").write_text(
        "Mount a generated test here to have it judged against both revisions:\n\n"
        f"    docker run -v ./candidate.py:{CANDIDATE} <image> "
        "evaluate-generated-test\n", encoding="utf-8")

    for name, body in scripts.items():
        path = tmp / "scripts" / name
        path.write_text(body, encoding="utf-8")
        if name.endswith(".sh"):
            path.chmod(0o755)

    (tmp / "metadata.json").write_text(json.dumps(meta, indent=2, default=str),
                                       encoding="utf-8")
    e = meta["environment"]
    (tmp / "Dockerfile").write_text(_DOCKERFILE.format(
        instance_id=meta["instance_id"], image=image, base_image=base_image,
        bench=BENCH, buggy_dir=BUGGY_DIR, fixed_dir=FIXED_DIR,
        buggy_commit=meta["buggy_revision"], fixed_commit=meta["fixed_revision"],
        python=e.get("python") or "?", numpy=e.get("numpy") or "-",
        scipy=e.get("scipy") or "-", arch=(e.get("arch") or "?").replace("\n", " ")),
        encoding="utf-8")
    (tmp / "README.md").write_text(_README.format(
        instance_id=meta["instance_id"], image=image,
        reference=meta.get("reference") or "-", category=meta.get("category") or "-",
        buggy_commit=meta["buggy_revision"], fixed_commit=meta["fixed_revision"],
        selector=meta["test"].get("command_buggy") or meta["test"].get("selector") or "-",
        candidate=CANDIDATE), encoding="utf-8")


def package(ctx, *, context, verdict, task: str, grading: str,
            opts: PackageOptions, hang_timeout: int = 0) -> PackageResult:
    report = ctx.report or {}
    status = report.get("status") or ""
    transition = report.get("transition")
    if not opts.enabled:
        return PackageResult(reason="packaging not requested")

    earned = status in opts.statuses and transition == "fail->pass"
    hang_ok = bool(hang_timeout) and transition == "timeout->pass" and \
        verdict is not None and verdict.reproduction == "F2P" and verdict.valid
    if not (earned or hang_ok):
        if opts.statuses and status not in opts.statuses:
            return PackageResult(reason=f"status {status or '?'} is not packaged "
                                        f"(want {', '.join(opts.statuses)})")
        return PackageResult(reason=f"transition {transition!r} is not fail->pass")

    spec = ctx.spec
    image = image_name(spec, opts)
    env = freeze_environment(ctx)
    buggy_cmd = (_test_record(ctx, "buggy") or {}).get("command") or spec.test_selector
    fixed_cmd = (_test_record(ctx, "fixed") or {}).get("command") or buggy_cmd
    scripts = _runner_scripts(buggy_cmd, fixed_cmd, spec.test_selector or "-",
                              hang_timeout=hang_timeout if hang_ok else 0)
    meta = build_metadata(ctx, context, verdict, env, image, task, grading,
                          hang_timeout=hang_timeout if hang_ok else 0)
    if hang_ok:
        meta["reproduction"]["harness_status"] = status
        meta["reproduction"]["status"] = "F2P_REPRODUCED (hang)"

    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        stage = Path(td) / "benchmark"
        stage.mkdir(parents=True)
        _stage(stage, meta, env, scripts, spec, image,
               getattr(spec, "image", "") or "debian:stable-slim")
        try:
            ctx.container.copy_in(stage, BENCH)
            ctx.container.run(f"chmod +x {BENCH}/scripts/*.sh", workdir="/",
                              timeout_s=120)
        except Exception as e:
            return PackageResult(reason=f"could not stage {BENCH}: "
                                        f"{type(e).__name__}: {e}")
        res = ctx.container.commit(image, changes=[
            f'ENTRYPOINT ["{BENCH}/scripts/entrypoint.sh"]',
            'CMD ["validate"]',
            f'WORKDIR {BENCH}',
            f'LABEL org.numbugbench.instance="{meta["instance_id"]}"',
            f'LABEL org.numbugbench.transition="fail->pass"',
            f'LABEL org.numbugbench.buggy="{spec.buggy_commit}"',
            f'LABEL org.numbugbench.fixed="{spec.fixed_commit}"',
        ])
        if getattr(res, "exit_code", 1) != 0:
            return PackageResult(reason=f"docker commit failed: "
                                        f"{(getattr(res, 'stderr', '') or '')[:200]}")
        artifacts = ""
        if opts.artifacts_dir:
            out = Path(opts.artifacts_dir) / meta["instance_id"]
            out.mkdir(parents=True, exist_ok=True)
            for item in stage.rglob("*"):
                rel = item.relative_to(stage)
                if item.is_dir():
                    (out / rel).mkdir(parents=True, exist_ok=True)
                else:
                    (out / rel).write_bytes(item.read_bytes())
            artifacts = str(out)
            try:
                from . import rebuild as _rebuild
                _tp = getattr(spec, "test_patch_path", "") or ""
                _rebuild.write_context(image, out / "rebuild",
                                       test_patch=Path(_tp) if _tp else None)
            except Exception as _e:
                (out / "rebuild").mkdir(parents=True, exist_ok=True)
                (out / "rebuild" / "ERROR.txt").write_text(
                    f"rebuild-from-source context failed: "
                    f"{type(_e).__name__}: {_e}\n", encoding="utf-8")
            size = image_size(image)
            (out / "image_size.txt").write_text(
                f"{image}\t{size} bytes\t{size / 1e9:.2f} GB\n"
                f"# the snapshot image was deleted after packaging to save disk; "
                f"rebuild it from source with `docker build rebuild/`.\n",
                encoding="utf-8")
            deleted = delete_image(image)
            ctx.emit("thought", f"packaged {image} ({size / 1e9:.2f} GB); recorded "
                                f"size and {'deleted' if deleted else 'could not delete'} "
                                f"the snapshot")
            return PackageResult(packaged=True, image=image, artifacts=artifacts,
                                 metadata=meta, size_bytes=size, image_deleted=deleted)
    return PackageResult(packaged=True, image=image, artifacts=artifacts,
                         metadata=meta, size_bytes=image_size(image))


def refresh_scripts(image: str, artifacts_dir: str = "") -> dict:
    import subprocess

    def docker(*args, **kw):
        return subprocess.run(["docker", *args], capture_output=True, text=True, **kw)

    meta_raw = docker("run", "--rm", "--entrypoint", "cat", image,
                      f"{BENCH}/metadata.json")
    if meta_raw.returncode != 0:
        return {"image": image, "refreshed": False,
                "reason": f"cannot read {BENCH}/metadata.json: "
                          f"{meta_raw.stderr.strip()[:160]}"}
    try:
        meta = json.loads(meta_raw.stdout)
    except ValueError as e:
        return {"image": image, "refreshed": False, "reason": f"bad manifest: {e}"}

    test = meta.get("test") or {}
    buggy_cmd = test.get("command_buggy") or test.get("selector") or ""
    fixed_cmd = test.get("command_fixed") or buggy_cmd
    if not buggy_cmd:
        return {"image": image, "refreshed": False,
                "reason": "the manifest records no test command"}
    scripts = _runner_scripts(buggy_cmd, fixed_cmd, test.get("selector") or "-")

    created = docker("create", "--entrypoint", "/bin/sh", image)
    if created.returncode != 0:
        return {"image": image, "refreshed": False,
                "reason": f"docker create failed: {created.stderr.strip()[:160]}"}
    cid = created.stdout.strip()
    try:
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as td:
            stage = Path(td) / "scripts"
            stage.mkdir()
            for name, body in scripts.items():
                path = stage / name
                path.write_text(body, encoding="utf-8")
                if name.endswith(".sh"):
                    path.chmod(0o755)
            cp = docker("cp", f"{stage}/.", f"{cid}:{BENCH}/scripts")
            if cp.returncode != 0:
                return {"image": image, "refreshed": False,
                        "reason": f"docker cp failed: {cp.stderr.strip()[:160]}"}
            if artifacts_dir:
                out = Path(artifacts_dir) / meta.get("instance_id", "") / "scripts"
                out.mkdir(parents=True, exist_ok=True)
                for name, body in scripts.items():
                    (out / name).write_text(body, encoding="utf-8")
        done = docker("commit",
                      "--change", f'ENTRYPOINT ["{BENCH}/scripts/entrypoint.sh"]',
                      "--change", 'CMD ["validate"]',
                      "--change", f"WORKDIR {BENCH}", cid, image)
        if done.returncode != 0:
            return {"image": image, "refreshed": False,
                    "reason": f"docker commit failed: {done.stderr.strip()[:160]}"}
    finally:
        docker("rm", "-f", cid)
    return {"image": image, "refreshed": True,
            "instance": meta.get("instance_id", ""),
            "scripts": sorted(scripts)}

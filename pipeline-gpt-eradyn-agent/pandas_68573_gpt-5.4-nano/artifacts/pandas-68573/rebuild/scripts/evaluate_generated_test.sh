#!/usr/bin/env bash
# Each revision owns its interpreter. Putting its .venv first is what makes
# `python` mean THIS revision's build rather than the other one's.
set -uo pipefail
# A hang instance's contract is not FAIL/PASS but "does not terminate within N
# seconds / PASS". N is declared in scripts/hang_timeout; absent or 0 means the
# ordinary contract. Both revisions get the same wrapper, so the two sides stay
# byte-for-byte comparable.
HANG_TIMEOUT="$(cat "$(dirname "$0")/hang_timeout" 2>/dev/null || echo 0)"
case "$HANG_TIMEOUT" in ''|*[!0-9]*) HANG_TIMEOUT=0 ;; esac
rev_dir() { [ "$1" = fixed ] && echo "/workspace/fixed" || echo "/workspace/buggy"; }
enter() {
  cd "$(rev_dir "$1")" || exit 90
  if [ -x "$PWD/.venv/bin/python" ]; then PATH="$PWD/.venv/bin:$PATH"; export PATH; fi
  if [ -f "$PWD/.numagent-constraints.txt" ]; then
    PIP_CONSTRAINT="$PWD/.numagent-constraints.txt"; export PIP_CONSTRAINT; fi
}

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

CAND="${1:-/benchmark/candidate/test.py}"
if [ ! -f "$CAND" ]; then
  echo "{\"error\": \"no candidate test at $CAND — mount one\"}"; exit 64
fi
judge() {      # $1 = buggy|fixed
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
  ( cd "$work" && python -m pytest test_candidate.py -q -rs -p no:randomly )       >"$out" 2>&1
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
    ( cd "$work" && PYTHONPATH="$(rev_dir "$1")" python -m pytest         test_candidate.py -q -rs -p no:randomly ) >"$out" 2>&1
  fi
  local code=$?
  classify "$code" "$out"
  if [ "${NUMBUGBENCH_SHOW_CANDIDATE:-0}" = 1 ]; then
    echo "--- $1 ---" >&2; cat "$out" >&2
  fi
  rm -rf "$out" "$work"
}
B="$(judge buggy)"; F="$(judge fixed)"
if [ "$B" = FAIL ] && [ "$F" = PASS ]; then CLASS=F2P
elif [ "$B" = PASS ] && [ "$F" = PASS ]; then CLASS=P2P
elif [ "$B" = FAIL ] && [ "$F" = FAIL ]; then CLASS=F2F
elif [ "$B" = PASS ] && [ "$F" = FAIL ]; then CLASS=P2F
else CLASS=INVALID; fi
printf '{"buggy": "%s", "fixed": "%s", "classification": "%s"}\n' "$B" "$F" "$CLASS"
[ "$CLASS" = F2P ] || exit 1

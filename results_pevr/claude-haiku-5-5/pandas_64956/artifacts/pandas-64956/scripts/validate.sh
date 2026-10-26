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
test_command() {
  cat "/benchmark/scripts/test_command_$1"
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

run_rev() {    # $1 = buggy|fixed
  enter "$1"
  local out; out="$(mktemp)"
  # `-rA` asks pytest for a summary naming EVERY case and its outcome, instead
  # of a row of dots. It has to go on the command LINE: PYTEST_ADDOPTS is
  # inserted before the command's own arguments, so a stored command carrying
  # `-rs` (scipy's do) wins and reports skips only. `-r` selects what the
  # summary lists — it changes neither what runs nor the exit code — so the
  # graded behaviour is still what this instance was validated with, and
  # `test_command_<rev>` keeps that command verbatim as the record.
  local cmd; cmd="$(test_command "$1")"
  case "$cmd" in
    *pytest*) case "$cmd" in *" -rA"*) ;; *) cmd="$cmd -rA" ;; esac ;;
  esac
  if [ "${HANG_TIMEOUT:-0}" -gt 0 ]; then
    # bash -c, not eval: the command may begin with env assignments
    # (`MPMATH_MAX_PREC=200000 python …`) which `timeout` cannot exec directly
    timeout -k 5 "$HANG_TIMEOUT" bash -c "$cmd" >"$out" 2>&1
  else
    # shellcheck disable=SC2086
    eval "$cmd" >"$out" 2>&1
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
    found=$(eval "$(test_command "$2") --collect-only" 2>/dev/null             | grep -E "::" | sort -u | head -60)
    [ -n "$found" ] && echo "    Cases (selected; outcomes above):" && {
      printf '%s
' "$found" | while IFS= read -r line; do echo "      $line"; done
      return
    }
  fi
  if [ -z "$found" ]; then
    echo "    Cases: (the runner printed no per-case names)"
    return
  fi
  echo "    Cases:"
  printf '%s
' "$found" | while IFS= read -r line; do echo "      $line"; done
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

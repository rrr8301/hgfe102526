# pandas-68487

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68487:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68487:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68487:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68487:eradyn-agent metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68487
Category: correctness
Buggy: d34d3e05636e8be8617d89d352f3d636558fbc12
Fixed: cacc8a11864a7e8d82e03b894b60a905ffffd973

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs pandas.tests.arrays.sparse.test_reductions -k test_frame_nan_result_dtype_matches_dense -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest pandas/tests/arrays/sparse/test_reductions.py -k test_frame_nan_result_dtype_matches_dense -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68487:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

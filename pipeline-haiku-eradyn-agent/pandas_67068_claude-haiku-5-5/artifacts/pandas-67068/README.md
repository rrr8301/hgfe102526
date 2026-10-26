# pandas-67068

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-67068:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-67068:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-67068:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-67068:eradyn-agent metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/67068
Category: special_value
Buggy: af290b6ae3e7b255663f46350eb5e75d32dce752
Fixed: e34122aa1b84ce133fd973c83ecac014d15dcf01

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs pandas.tests.test_nanops -k test_nanops_reductions_dont_skip_nan_with_mask -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest pandas/tests/test_nanops.py -k test_nanops_reductions_dont_skip_nan_with_mask -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-67068:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

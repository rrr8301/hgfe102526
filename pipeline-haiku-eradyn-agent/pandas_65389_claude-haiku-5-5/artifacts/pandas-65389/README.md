# pandas-65389

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-65389:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-65389:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-65389:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-65389:eradyn-agent metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/65389
Category: accuracy
Buggy: 94fe9d6d03029ca6c8d244f6c70e89e97845265a
Fixed: 32e6988fa84deabcc2baba117ef2b75e7838f491

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs pandas.tests.arrays.test_timedeltas -k test_total_seconds_stays_strictly_inside_integer_seconds -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest pandas/tests/arrays/test_timedeltas.py -k test_total_seconds_stays_strictly_inside_integer_seconds -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-65389:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

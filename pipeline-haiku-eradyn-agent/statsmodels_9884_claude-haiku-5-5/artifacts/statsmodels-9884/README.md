# statsmodels-9884

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-9884:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-9884:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-9884:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-9884:eradyn-agent metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/9884
Category: convergence
Buggy: 8cd2519cfb7ddb84dc4b9d3f677cdc480e09f369
Fixed: c8397561fa1f6f46887b8b0523b48d1bec3555b4

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs statsmodels.stats.tests.test_power -k test_solve_power_no_solution_returns_nan -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest statsmodels/stats/tests/test_power.py -k test_solve_power_no_solution_returns_nan -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-9884:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

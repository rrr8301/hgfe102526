# statsmodels-9903

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-9903:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-9903:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-9903:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-9903:eradyn-agent metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/9903
Category: correctness
Buggy: c1482430228010566b31b6f3f7e0b1c1a08e920c
Fixed: 071a14d7a282e14b022f215a41a6c80168f475df

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs statsmodels.stats.tests.test_diagnostic -k test_rainbow_use_distance_order_invariant -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest statsmodels/stats/tests/test_diagnostic.py -k test_rainbow_use_distance_order_invariant -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-9903:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

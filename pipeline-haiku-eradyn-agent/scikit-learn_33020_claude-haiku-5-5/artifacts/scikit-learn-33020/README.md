# scikit-learn-33020

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-33020:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-33020:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-33020:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-33020:eradyn-agent metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/33020
Category: accuracy
Buggy: c83733128e2ca835674442927dfbbe2c3da93db0
Fixed: ce4f2340fecb280fc33163a1437042e1d98f2356

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs sklearn.linear_model.tests.test_ridge -k test_ridge_gcv_integer_arrays -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest sklearn/linear_model/tests/test_ridge.py -k test_ridge_gcv_integer_arrays -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-33020:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

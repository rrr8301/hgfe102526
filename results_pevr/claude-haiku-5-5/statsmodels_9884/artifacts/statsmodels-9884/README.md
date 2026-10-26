# statsmodels-9884

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-9884:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-9884:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-9884:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-9884:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/9884
Category: convergence
Buggy: 8cd2519cfb7ddb84dc4b9d3f677cdc480e09f369
Fixed: c8397561fa1f6f46887b8b0523b48d1bec3555b4

Test: python -I -m pytest --pyargs statsmodels.stats.tests.test_power -k test_solve_power_no_solution_returns_nan -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-9884:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

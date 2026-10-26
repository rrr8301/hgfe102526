# pandas-63143

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-63143:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-63143:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-63143:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-63143:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/63143
Category: accuracy
Buggy: eaabb41d17ca5757e8a4c0f3a2f1914a688bf3bd
Fixed: 55864a0a2a8833b9b00b17236aa93c1b859051ef

Test: python -I -m pytest --pyargs pandas.tests.window.test_rolling -k test_same_result_with_different_lengths -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-63143:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

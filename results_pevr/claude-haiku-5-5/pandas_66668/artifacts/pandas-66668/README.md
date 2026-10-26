# pandas-66668

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66668:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66668:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66668:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66668:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66668
Category: correctness
Buggy: e121ab99172d6342587d2e2d6dcb1bc848083a3b
Fixed: 1e2f5c4badf08cd7a6acafa432208a3f51d86c55

Test: python -I -m pytest --pyargs pandas.tests.scalar.timedelta.test_arithmetic -k test_td_mul_int_ndarray_overflow -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66668:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# pandas-64956

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-64956:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-64956:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-64956:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-64956:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/64956
Category: special_value
Buggy: af4b00bf45d97b3307ab4f9f58ad78cf5ce23824
Fixed: ff8dac94dcc6964255a6b44048a80bfd0d015ce3

Test: python -I -m pytest --pyargs pandas.tests.arrays.test_array -k test_pd_array_float_with_nan_to_integer_raises -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-64956:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

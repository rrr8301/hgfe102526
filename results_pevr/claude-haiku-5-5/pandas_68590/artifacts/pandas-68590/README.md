# pandas-68590

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68590:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68590:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68590:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68590:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68590
Category: accuracy
Buggy: 14de06cf28232d7b5cc2b1a2b58fbc59a815db2d
Fixed: 4478d75cc3b5a1015499f81ca297792a2afa6a5e

Test: python -I -m pytest --pyargs pandas.tests.arrays.sparse.test_array -k test_to_dense_datetimelike_non_numpy_fill -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68590:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

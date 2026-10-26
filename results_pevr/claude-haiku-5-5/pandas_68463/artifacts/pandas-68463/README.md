# pandas-68463

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68463:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68463:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68463:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68463:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68463
Category: correctness
Buggy: 6ce79779b18c834b0d501f533bea3195be21d18e
Fixed: 2babb371105ab7a16f2b294ff228fdb17184c722

Test: python -I -m pytest --pyargs pandas.tests.arrays.sparse.test_astype -k test_astype_datetimelike_to_sparse_int64_non_na_fill_value -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68463:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

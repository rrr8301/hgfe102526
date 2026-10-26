# pandas-66323

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66323:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66323:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66323:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66323:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66323
Category: special_value
Buggy: 61cbe5f9c92c7ce2c95c4d457ae038e4ab017293
Fixed: cf4e6e89367416784747671ebc6e2495701030d3

Test: python -I -m pytest --pyargs pandas.tests.dtypes.test_missing -k test_array_equivalent_complex_nan_component -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66323:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

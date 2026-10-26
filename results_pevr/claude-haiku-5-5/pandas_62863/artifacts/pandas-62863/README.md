# pandas-62863

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-62863:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-62863:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-62863:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-62863:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/62863
Category: accuracy
Buggy: 65a2a749773a4ee11b07048143ab03bc4846fd72
Fixed: 88c276a489abd1742c84ca1cafedb1b9e93f083a

Test: python -I -m pytest --pyargs pandas.tests.window.test_rolling -k test_rolling_skew_kurt_large_value_range -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-62863:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

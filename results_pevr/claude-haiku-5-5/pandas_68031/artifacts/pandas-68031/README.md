# pandas-68031

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68031:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68031:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68031:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68031:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68031
Category: special_value
Buggy: bd801af80f5a2f873cc7bd93d04ebc638698de9c
Fixed: 04dff44f6224b15ed647ad5cd865905bca8b116e

Test: python -I -m pytest --pyargs pandas.tests.io.pytables.test_select -k test_select_ordering_comparison_skips_nat -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68031:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

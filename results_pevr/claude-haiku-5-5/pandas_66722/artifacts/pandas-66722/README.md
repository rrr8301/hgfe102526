# pandas-66722

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66722:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66722:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66722:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66722:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66722
Category: accuracy
Buggy: 8cb88cf1fbd02c747abd1e5d51201bd379c27b71
Fixed: 20c379a35b05f9cf453df2bb66adb70358ec123b

Test: python -I -m pytest --pyargs pandas.tests.util.test_assert_almost_equal -k test_assert_almost_equal_large_mixed_integer_float_atol -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66722:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

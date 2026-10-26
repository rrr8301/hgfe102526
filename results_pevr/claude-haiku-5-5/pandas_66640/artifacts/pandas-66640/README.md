# pandas-66640

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66640:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66640:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66640:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66640:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66640
Category: accuracy
Buggy: e7ed83ad33600979f659d2f946d016743d7d0c24
Fixed: 9fcddfbcad41d4d1cf13e355915a83a95f3e7565

Test: python -I -m pytest --pyargs pandas.tests.arithmetic.test_timedelta64 -k test_td64arr_integral_float_op_is_exact -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66640:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

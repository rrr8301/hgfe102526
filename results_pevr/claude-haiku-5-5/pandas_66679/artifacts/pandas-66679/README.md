# pandas-66679

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66679:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66679:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66679:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66679:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66679
Category: correctness
Buggy: dd2c4673617ed79550e9b5c967305a6fe013bc40
Fixed: ebd40365c94b2d34ce56ff5bfe1a527ed1bb7f49

Test: python -I -m pytest --pyargs pandas.tests.arithmetic.test_datetime64 -k test_dt64arr_add_day_lands_on_nat_sentinel -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66679:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

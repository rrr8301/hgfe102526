# pandas-62813

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-62813:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-62813:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-62813:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-62813:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/62813
Category: correctness
Buggy: 27368892d3740197be1fe02959f34e2567f832bd
Fixed: 0350b56dd5d7178bb2347a03f7bd3f8c44c1a90b

Test: python -I -m pytest --pyargs pandas.tests.dtypes.test_inference -k test_convert_numeric_overflow -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-62813:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

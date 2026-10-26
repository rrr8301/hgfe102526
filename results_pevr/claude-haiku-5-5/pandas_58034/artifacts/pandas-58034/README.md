# pandas-58034

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-58034:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-58034:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-58034:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-58034:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/58034
Category: correctness
Buggy: 7e8d492b26bb933884492ffcb5ce8fc501c281d5
Fixed: 40e621fc9f77d01e80516a8ca99294386a438958

Test: python -I -m pytest --pyargs pandas.tests.tools.test_to_datetime -k test_unit_str -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-58034:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

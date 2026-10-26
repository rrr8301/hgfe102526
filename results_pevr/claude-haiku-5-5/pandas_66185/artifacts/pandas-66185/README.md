# pandas-66185

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66185:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66185:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66185:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66185:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66185
Category: correctness
Buggy: dce860fccdf9994809c16146eaa7354bff598cb3
Fixed: cc4813e776335e09abe7ca3e91d8f0925049d919

Test: python -I -m pytest --pyargs pandas.tests.tools.test_to_datetime -k test_float_to_datetime_near_int64_boundary -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66185:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# pandas-68688

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68688:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68688:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68688:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68688:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68688
Category: correctness
Buggy: 7cafb46e9665078e2a423ce61b5902cf9e1d537e
Fixed: 533889e01ebe40856cfbdbaff13b52e2e4f62e06

Test: python -I -m pytest --pyargs pandas.tests.frame.methods.test_astype -k test_astype_object_numeric_to_timedelta64_unit_2d -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68688:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

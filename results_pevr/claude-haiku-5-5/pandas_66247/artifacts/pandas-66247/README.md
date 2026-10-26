# pandas-66247

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66247:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66247:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66247:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66247:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66247
Category: correctness
Buggy: 20542d17b1ccd5ce2b1317196c49c975c040f764
Fixed: 2421248b80f4eb22e081265b6c8e034585075685

Test: python -I -m pytest --pyargs pandas.tests.scalar.timedelta.test_constructors -k test_float_inf_raises -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66247:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

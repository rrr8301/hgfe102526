# pandas-66823

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66823:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66823:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66823:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66823:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66823
Category: special_value
Buggy: 5929ccb321ddae1d7570b95dd736b7c529e88774
Fixed: 0cccc8f2cebb20093e109179b593f87e43db393d

Test: python -I -m pytest --pyargs pandas.tests.scalar.timedelta.test_constructors -k test_construct_from_kwargs_inf -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66823:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

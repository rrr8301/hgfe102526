# scikit-learn-33020

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-33020:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-33020:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-33020:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-33020:latest metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/33020
Category: accuracy
Buggy: c83733128e2ca835674442927dfbbe2c3da93db0
Fixed: ce4f2340fecb280fc33163a1437042e1d98f2356

Test: python -I -m pytest --pyargs sklearn.linear_model.tests.test_ridge -k test_ridge_gcv_integer_arrays -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-33020:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

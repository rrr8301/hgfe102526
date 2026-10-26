# scikit-learn-30514

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-30514:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-30514:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-30514:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-30514:latest metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/30514
Category: correctness
Buggy: 692289e4939d5872c57e3e1044565018396c3a90
Fixed: 5f91dca9f0ded71df9f82e7f120f0263f15fb18e

Test: python -I -m pytest --pyargs sklearn.manifold.tests.test_mds -k test_nonmetric_lower_normalized_stress -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-30514:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

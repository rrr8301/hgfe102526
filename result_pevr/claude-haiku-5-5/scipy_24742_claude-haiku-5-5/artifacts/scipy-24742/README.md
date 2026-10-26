# scipy-24742

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-24742:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-24742:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-24742:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-24742:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/24742
Category: correctness
Buggy: cbef678e05c5a30b53b7d05606ed3dde074f7b48
Fixed: 0a86361920dc920e8fe9089f5591a9f6ec76d657

Test: python -I -m pytest --pyargs scipy.stats.tests.test_stats -k test_linregress_two_points_nan_inference -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-24742:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

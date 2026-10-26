# astropy-18348

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-18348:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-18348:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-18348:latest fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-18348:latest metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/18348
Category: special_value
Buggy: ed0072408b14460f9ee3274c8510b46126a0bfa8
Fixed: 4e88fe05f3ff5629c1e0806e5f839b4f43a74894

Test: python -I -m pytest --pyargs astropy.convolution.tests.test_convolve -k test_regression_16497_nan_treatment_consistency -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-18348:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

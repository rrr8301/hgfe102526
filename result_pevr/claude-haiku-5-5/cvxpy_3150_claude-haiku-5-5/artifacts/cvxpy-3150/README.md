# cvxpy-3150

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3150:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3150:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3150:latest fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3150:latest metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3150
Category: correctness
Buggy: ba65d57b2d663fd24aad211f128545f762dd635a
Fixed: b73a8407afdb8f98d5048e9a7ef7d75f90372726

Test: python -m pytest cvxpy/tests/test_quad_form.py -k test_indefinite_assume_psd_raises -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3150:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

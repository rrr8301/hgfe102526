# cvxpy-3376

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3376:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3376:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3376:latest fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3376:latest metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3376
Category: correctness
Buggy: 6a27fc27f80d810d74332e9074522b024cfe702d
Fixed: f9e6958f3b580b5b9b502621cddb2bc01f1e8d02

Test: python -m pytest cvxpy/tests/test_complex.py -k test_lambda_sum_largest_real_arg -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3376:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

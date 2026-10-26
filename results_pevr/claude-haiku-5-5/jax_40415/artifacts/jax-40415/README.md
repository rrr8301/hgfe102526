# jax-40415

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-40415:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-40415:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-40415:latest fixed      # the same test on the fixed revision
    docker run numbugbench/jax-40415:latest metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/40415
Category: accuracy
Buggy: cc95c6c81dc9e1384375dc0e4c4d5245a72f8313
Fixed: 6af60b85a2e49341bcfa8c219b02bc119bd55256

Test: python -m pytest tests/lax_test.py -k testTanhGradSymmetry -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-40415:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

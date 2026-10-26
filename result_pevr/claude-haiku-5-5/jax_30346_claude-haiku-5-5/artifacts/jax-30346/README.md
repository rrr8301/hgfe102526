# jax-30346

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-30346:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-30346:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-30346:latest fixed      # the same test on the fixed revision
    docker run numbugbench/jax-30346:latest metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/30346
Category: accuracy
Buggy: 1b556f2e7444e80952fb923e6f4ad16c55ba1a23
Fixed: 893b483e3f130bad7db8a0858dc229033e9ff9e1

Test: python -m pytest tests/lax_test.py -k testExpm1Grad -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-30346:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

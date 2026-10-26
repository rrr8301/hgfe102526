# jax-24074

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-24074:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-24074:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-24074:latest fixed      # the same test on the fixed revision
    docker run numbugbench/jax-24074:latest metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/24074
Category: correctness
Buggy: 437d7d88b992c9f511a3019823104d176e84064f
Fixed: 5b839ff3c63cd5d1372671220c3befd0e1c49d21

Test: python -m pytest tests/lax_numpy_test.py -k testPolyfit -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-24074:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

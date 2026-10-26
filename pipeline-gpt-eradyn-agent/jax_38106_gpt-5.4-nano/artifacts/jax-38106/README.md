# jax-38106

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-38106:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-38106:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-38106:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-38106:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/38106
Category: special_value
Buggy: 8a0456ae6a32d8def8166e91a07208ed4b218d57
Fixed: 665882239d8c0d7c84c80e9c52046f8cfe163997

Test: python -m pytest tests/lax_numpy_operators_test.py -k test_heaviside_nan -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-38106:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

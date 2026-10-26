# jax-20754

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-20754:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-20754:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-20754:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-20754:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/20754
Category: special_value
Buggy: eabd1bc7fcf4c1dfb450b4406887135e394f3bb3
Fixed: 5f22b12576199067e86f2cade2f126183e9ce773

Test: pytest -q tests/lax_numpy_test.py -k testHypotComplexInputDeprecation

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-20754:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

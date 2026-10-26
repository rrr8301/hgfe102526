# jax-25038

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-25038:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-25038:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-25038:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-25038:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/25038
Category: accuracy
Buggy: e707edeafaf1c9d71b2b172596c95299474d04a0
Fixed: 96c012990de86ccb0eb815a11ae4e2c337802794

Test: python -m pytest tests/scipy_stats_test.py -k testGammaDebugNans -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-25038:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

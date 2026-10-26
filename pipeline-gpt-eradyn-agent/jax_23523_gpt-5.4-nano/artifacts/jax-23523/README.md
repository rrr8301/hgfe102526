# jax-23523

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-23523:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-23523:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-23523:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-23523:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/23523
Category: special_value
Buggy: 72c095261fb2d678005bdaa963d4146d11634af0
Fixed: 7d2f0a75c1c10aeae63ed1a0ced17b7aeb979989

Test: JAX_PLATFORMS=gpu PYTHONWARNINGS=default python -m pytest tests/pallas/ops_test.py -k test_sign -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-23523:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# jax-37457

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-37457:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-37457:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-37457:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-37457:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/37457
Category: special_value
Buggy: 837f78e592d58325cc095211da7f91dece4c1523
Fixed: efbd24c780549d3863cce9530f5fd203808d2470

Test: python -m pytest tests/nn_test.py -k testDotProductAttentionFloat64MaskDebugInfs -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-37457:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

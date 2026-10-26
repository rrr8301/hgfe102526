# jax-20885

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-20885:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-20885:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-20885:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-20885:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/20885
Category: correctness
Buggy: 8ead2df7bb3b1dc8103b77404bb6d55a7a519982
Fixed: 8b1418244b8599c56b4a530f09b34529c82c1750

Test: python -m pytest tests/scipy_stats_test.py -k testBetaLogPdfNegativeConstants -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-20885:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# jax-25791

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-25791:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-25791:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-25791:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/jax-25791:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/25791
Category: special_value
Buggy: 8f2f4b45fb07b2e1ad74909573142b8119471fa6
Fixed: aed79707e29451156058025c0a5d349493fd8669

Test: python -m pytest tests/lax_scipy_test.py -k testLogSumExpWhereGrad -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-25791:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

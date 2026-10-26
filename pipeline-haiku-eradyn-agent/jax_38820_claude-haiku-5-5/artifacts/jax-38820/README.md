# jax-38820

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-38820:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-38820:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-38820:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/jax-38820:eradyn-agent metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/38820
Category: special_value
Buggy: 2ed31913ce07da6d4abbc1c2397b24481d971553
Fixed: da23cf20a2880667a6f04e818ee58b901fbb5da7

Test: python -m pytest tests/lax_scipy_special_functions_test.py -k 'testGammaIncBoundaryValues or testGammaIncCBoundaryValues' -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-38820:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

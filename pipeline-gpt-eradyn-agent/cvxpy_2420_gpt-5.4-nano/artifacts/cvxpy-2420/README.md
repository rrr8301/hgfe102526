# cvxpy-2420

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-2420:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-2420:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-2420:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-2420:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/2420
Category: correctness
Buggy: c61ac6aa369400c1e633e4b93fa34296cb697753
Fixed: dda6ee442192753a91ca639263d13367348423b3

Test: python -m pytest cvxpy/tests/test_canon_sign.py -k test_maximum_sign -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-2420:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

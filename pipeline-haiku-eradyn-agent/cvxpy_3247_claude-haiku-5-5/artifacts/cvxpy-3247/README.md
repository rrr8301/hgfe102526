# cvxpy-3247

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3247:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3247:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3247:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3247:eradyn-agent metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3247
Category: correctness
Buggy: b5a20484ff95e63749021d21f2004894f5c48dd9
Fixed: 5b45a71d79ce9df6a30aaec2bb2e0f37fb40c2f8

Test: python -m pytest cvxpy/tests/test_grad.py -k matrix_frac_matrix_x -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3247:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

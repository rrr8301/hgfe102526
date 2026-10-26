# cvxpy-3180

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3180:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3180:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3180:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3180:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3180
Category: correctness
Buggy: a08ce56ad4658e1f82de01a774697cfd55786702
Fixed: 96e72f783d53725482ca0cac5d092cb5eb71f23b

Test: python -m pytest cvxpy/tests/test_atoms.py -k test_log_det_sign -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3180:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

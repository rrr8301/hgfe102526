# cvxpy-3313

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3313:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3313:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3313:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3313:eradyn-agent metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3313
Category: accuracy
Buggy: 4a921b5329ecad7840aef010b14152653c3d7e6e
Fixed: a52742bbbbf9167d7daec811b32810ca61c0d39e

Test: python -m pytest cvxpy/tests/test_linalg_utils.py -k test_singular_leading_minor_falls_back_to_dense_ldl -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3313:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

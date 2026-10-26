# cvxpy-3220

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3220:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3220:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3220:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3220:eradyn-agent metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3220
Category: correctness
Buggy: 8fb412018aefc516a50bd0261c883f8071023595
Fixed: a08ce56ad4658e1f82de01a774697cfd55786702

Test: python -m pytest cvxpy/tests/nlp_tests/test_huber_sum_largest.py -k test_warmstart_feasibility -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3220:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

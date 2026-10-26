# cvxpy-3390

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3390:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3390:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3390:latest fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3390:latest metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3390
Category: correctness
Buggy: 21182ce8ffb5b8404d2887b0d06de4d6ea82d604
Fixed: 98b84cd1ffae585df8fd4cce1c83fb68299614e9

Test: python -m pytest cvxpy/tests/test_conic_solvers.py -k 'test_cvxopt_presolve_utility or test_cvxopt_presolve_opposite_inequalities or test_cvxopt_presolve_merged_inequality_duals or test_compress_matrix_eliminates_empty_and_duplicate_rows' -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3390:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

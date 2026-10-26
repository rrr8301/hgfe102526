# cvxpy-3513

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3513:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3513:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3513:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3513:eradyn-agent metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3513
Category: correctness
Buggy: a4b9e106004c66e907aeba0379985d66cc735afa
Fixed: fb341062e31f5125296aa5fe06561f476d8ae0c9

Test: python -m pytest cvxpy/tests/test_conic_solvers.py cvxpy/tests/test_KKT.py cvxpy/tests/test_domain.py cvxpy/tests/test_dqcp.py cvxpy/tests/test_problem.py cvxpy/tests/test_semidefinite_vars.py -k "test_scs_linear_solver_options or test_scs_explicit_linear_solver or test_ecos_exp_soc_1 or test_scs_options or test_kkt_symmetric_var or test_partial_problem or test_dist_ratio or test_psd_constraint_bug or test_variable_name_conflict or test_sdp_problem" -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3513:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# cvxpy-3267

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/cvxpy-3267:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/cvxpy-3267:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/cvxpy-3267:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/cvxpy-3267:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/cvxpy/cvxpy/pull/3267
Category: special_value
Buggy: 9cd2f3d91765cc4eb867f9af76112c30dc60d229
Fixed: 4cdf93896f6651b1ce2c5891bbb55b463d21cb26

Test: pytest cvxpy/tests/test_bounds.py -k test_mul_bounds_zero_times_inf -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/cvxpy-3267:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

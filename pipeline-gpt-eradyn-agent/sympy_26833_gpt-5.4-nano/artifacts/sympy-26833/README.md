# sympy-26833

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-26833:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-26833:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-26833:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-26833:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/26833
Category: accuracy
Buggy: c1b1d2d701645ef6d4c68dd566d0a16dcf50d321
Fixed: 2e489cf4b1438ae134ba98a44a80cc9add1306b0

Test: python -m pytest sympy/matrices/tests/test_matrixbase.py -k test_inverse_symbolic_float_issue_26821 -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-26833:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

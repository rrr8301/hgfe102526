# sympy-28790

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-28790:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-28790:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-28790:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-28790:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/28790
Category: correctness
Buggy: 3e18ad01a1e420738c87719a4a555c8c1de1fe95
Fixed: 0558ee030c6e8047e5cfb93358984b4b6a8edfa3

Test: python -m pytest sympy/core/tests/test_evalf.py -k test_issue_28280 -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-28790:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# sympy-28567

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-28567:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-28567:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-28567:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-28567:eradyn-agent metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/28567
Category: correctness
Buggy: 22779eb67cb200a2dc224f6d04f5e07435b3b66b
Fixed: 48fa24a60d29e3a73113f1bee7d8e854c358bd22

Test: python -m pytest sympy/ntheory/tests/test_elliptic_curve.py -k test_elliptic_curve -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-28567:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

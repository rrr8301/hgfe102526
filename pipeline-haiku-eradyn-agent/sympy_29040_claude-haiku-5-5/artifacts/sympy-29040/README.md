# sympy-29040

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-29040:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-29040:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-29040:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-29040:eradyn-agent metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/29040
Category: correctness
Buggy: 841185289d56b5cbdb7fb5b9718c8c97b6339982
Fixed: 8ff0458f564bb8b2fb6b8777f27444beab1fcefd

Test: python -m pytest sympy/matrices/tests/test_decompositions.py -k test_QR_symbolic_dependent_columns -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-29040:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

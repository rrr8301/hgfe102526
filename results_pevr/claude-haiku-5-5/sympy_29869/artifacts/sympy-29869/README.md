# sympy-29869

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-29869:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-29869:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-29869:latest fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-29869:latest metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/29869
Category: correctness
Buggy: 6fcc30bbfa6fa4cbde2124640f1f84385292c6a6
Fixed: b1daadbe358004abce13b225690612c94cc402dc

Test: python -m pytest sympy/ntheory/tests/test_elliptic_curve.py -k test_elliptic_curve -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-29869:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

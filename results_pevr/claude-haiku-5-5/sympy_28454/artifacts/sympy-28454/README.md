# sympy-28454

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-28454:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-28454:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-28454:latest fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-28454:latest metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/28454
Category: correctness
Buggy: d8714262c2477c136a96f5fc19ebcb88946d9dc4
Fixed: 58c2aeedc07329ba1bfdebcf5dca63219f2db1b1

Test: python -m pytest sympy/matrices/tests/test_matrixbase.py -k test_multiplication -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-28454:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

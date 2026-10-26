# sympy-28511

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-28511:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-28511:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-28511:latest fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-28511:latest metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/28511
Category: special_value
Buggy: ac6661b9cbbe293ebcbdae8445163690214195ce
Fixed: ed2a9bef3b6e936d916f4021a8676422ac6ba29d

Test: python -m pytest sympy/integrals/tests/test_integrals.py -k test_integration_of_piecewise_with_simbolic_boundaries -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-28511:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# sympy-27503

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-27503:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-27503:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-27503:latest fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-27503:latest metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/27503
Category: accuracy
Buggy: bc72e87df6707c1fb98e3b8a67a6e8691cece608
Fixed: 90924581c3a2a559493287d315749bd561e55233

Test: python -m pytest sympy/polys/tests/test_densearith.py -k test_dup_mul_poly -q -rs -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-27503:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# numba-10528

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numba-10528:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numba-10528:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numba-10528:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numba-10528:latest metadata   # the manifest

Bug: https://github.com/numba/numba/pull/10528
Category: special_value
Buggy: 0d0938a717dbe464dfda3448f3f8571d2c606978
Fixed: 43b83d9a0ea3c07108cb73484fcbcc5284615958

Test: python -I -m pytest --pyargs numba.tests.test_complex -k test_mul -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numba-10528:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

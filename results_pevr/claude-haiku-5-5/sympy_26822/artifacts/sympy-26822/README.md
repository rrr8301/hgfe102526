# sympy-26822

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-26822:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-26822:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-26822:latest fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-26822:latest metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/26822
Category: accuracy
Buggy: 66c229001b7d1a02870e96ce4b0c92cb5fe1b6c9
Fixed: b65c687588bab22db54c7984231cf72be115fb34

Test: python -m pytest sympy/matrices/tests/test_matrixbase.py -k test_inverse_symbolic_float_issue_26821 -q -rs -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-26822:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

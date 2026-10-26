# scipy-20168

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20168:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20168:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20168:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20168:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20168
Category: convergence
Buggy: ecbd58a7ad953f48d3aa4808bb30bc9f3ed398f4
Fixed: ed00eb8d03e9e13a71d1933a0074a38ec512b0e9

Test: python -I -m pytest --pyargs scipy.optimize.tests.test_nnls -k test_nnls_inner_loop_case1 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20168:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

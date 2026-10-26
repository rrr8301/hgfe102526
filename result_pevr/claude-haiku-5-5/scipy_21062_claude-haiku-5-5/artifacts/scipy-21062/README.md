# scipy-21062

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21062:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21062:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21062:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21062:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21062
Category: correctness
Buggy: b3cf42e98594904a49f65ce0c37484d7a00fa2e7
Fixed: 24f4f89b80df3a987bf707248d55c031f14917d7

Test: python -I -m pytest --pyargs scipy.special.tests.test_specfun -k test_pro_rad1 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21062:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

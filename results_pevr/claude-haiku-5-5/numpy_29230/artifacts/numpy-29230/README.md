# numpy-29230

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-29230:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-29230:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-29230:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-29230:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/29230
Category: correctness
Buggy: f15a116ee12ec539c7ed1fafb3a0abfb82005e0f
Fixed: 7a74e12558789ecf0252dad56a77560db7411d13

Test: python -I -m pytest --pyargs numpy._core.tests.test_numeric -k test_unsigned_resize -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-29230:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

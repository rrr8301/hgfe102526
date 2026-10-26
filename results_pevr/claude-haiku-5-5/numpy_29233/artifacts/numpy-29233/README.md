# numpy-29233

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-29233:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-29233:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-29233:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-29233:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/29233
Category: correctness
Buggy: 0481076809a56806dac995af5319b952a2ca0109
Fixed: 70b6cdacda4c7c78be23f1349f872ce12fd49269

Test: python -I -m pytest --pyargs numpy._core.tests.test_numeric -k test_unsigned_resize -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-29233:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

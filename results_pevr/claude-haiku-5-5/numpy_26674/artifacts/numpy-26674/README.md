# numpy-26674

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-26674:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-26674:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-26674:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-26674:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/26674
Category: correctness
Buggy: 874e163bbd6b3a6229f30caea7bde71ac403476f
Fixed: 8736d8185df03432569f492568399f5544efd066

Test: python -I -m pytest --pyargs numpy._core.tests.test_dtype -k test_invalid_types -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-26674:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

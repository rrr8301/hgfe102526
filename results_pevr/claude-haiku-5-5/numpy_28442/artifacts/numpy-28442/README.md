# numpy-28442

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-28442:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-28442:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-28442:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-28442:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/28442
Category: special_value
Buggy: f091aba6f5c5c107a2449ad8304bced3f252a7f4
Fixed: 432d9b27e205a43cc76e328735c27c23dd33aed3

Test: python -I -m pytest --pyargs numpy._core.tests.test_multiarray -k test_dot_errstate -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-28442:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

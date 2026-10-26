# numpy-29500

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-29500:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-29500:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-29500:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-29500:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/29500
Category: correctness
Buggy: b7e5173c13d1e773d526b0484d697163f9e41a15
Fixed: c6eac44e914413ce48439d2435abbfd493149485

Test: python -I -m pytest --pyargs numpy.linalg.tests.test_linalg -k test_cond_dtypes -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-29500:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

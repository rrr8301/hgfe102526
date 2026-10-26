# numpy-31572

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-31572:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-31572:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-31572:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-31572:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/31572
Category: special_value
Buggy: b1b77568be65ee88b739ec54730e906a691edbdb
Fixed: 9bc0b7bf88bc0bc4b71ffba38783b0400f98449d

Test: python -I -m pytest --pyargs numpy._core.tests.test_multiarray -k test_sort_descending_complex_lexorder -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-31572:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

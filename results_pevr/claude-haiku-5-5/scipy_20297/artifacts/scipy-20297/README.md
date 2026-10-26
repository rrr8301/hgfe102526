# scipy-20297

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20297:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20297:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20297:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20297:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20297
Category: accuracy
Buggy: 237b6915e37ed433c18e87952cb3b8c4880ad774
Fixed: bb92c8014e21052e7dde67a76b28214dd1dcb94a

Test: python -I -m pytest --pyargs scipy.linalg.tests.test_basic -k test_zero_eigenvalue -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20297:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

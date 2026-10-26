# scipy-21577

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21577:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21577:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21577:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21577:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21577
Category: correctness
Buggy: 2d98ecc1f517158ab9651b0f24f098fe5d9c3e69
Fixed: 1ea8b5ed11dafb361ccaf0c9bca5414dc1700014

Test: python -I -m pytest --pyargs scipy.special.tests.test_basic -k test_hankel2_gh4517 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21577:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

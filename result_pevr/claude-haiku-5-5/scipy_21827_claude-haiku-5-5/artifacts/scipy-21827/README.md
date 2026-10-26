# scipy-21827

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21827:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21827:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21827:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21827:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21827
Category: special_value
Buggy: c45b3ba3df5ea9ebb517e98d62d138d0f9164724
Fixed: e5b0af896ef19285677e891f23d338d0b3ba16ed

Test: python -I -m pytest --pyargs scipy.special.tests.test_basic -k test_poles -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21827:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

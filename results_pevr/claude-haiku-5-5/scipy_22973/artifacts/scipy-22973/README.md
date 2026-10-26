# scipy-22973

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-22973:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-22973:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-22973:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-22973:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/22973
Category: accuracy
Buggy: a011b598d923bb72893773a27adbba02779a4d8d
Fixed: 9b92c55c8dbf71ad01371d04c17a7c3cd5956613

Test: python -I -m pytest --pyargs scipy.interpolate.tests.test_bsplines -k test_issue_22704 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-22973:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

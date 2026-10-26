# scipy-24172

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-24172:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-24172:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-24172:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-24172:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/24172
Category: accuracy
Buggy: 1b2e87dbc681b1d540362329f108a32df55bc514
Fixed: e5ccf400d3affa6924d8e51cb50416a79aae3dd3

Test: python -I -m pytest --pyargs scipy.stats.tests.test_morestats -k test_gh24172 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-24172:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

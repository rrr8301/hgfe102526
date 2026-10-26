# pandas-64809

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-64809:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-64809:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-64809:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-64809:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/64809
Category: accuracy
Buggy: b9cc4a636d004c3343fb27610df7984748c249c3
Fixed: 7228fdd6736864734da9fd821b33cbb9cd0bc330

Test: python -I -m pytest --pyargs pandas.tests.tseries.offsets.test_offsets -k test_add_dt64_non_nano_with_offset_parameter -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-64809:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

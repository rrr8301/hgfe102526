# pandas-66753

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66753:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66753:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66753:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66753:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66753
Category: correctness
Buggy: 5faf9dff8f3f08462ce3e0f72b44731ca10ac291
Fixed: f040cfd389619846a630107e1953e334f10b04ea

Test: python -I -m pytest --pyargs pandas.tests.indexes.datetimes.methods.test_tz_convert -k test_dti_utc_to_local_underflow -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66753:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

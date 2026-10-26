# pandas-63925

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-63925:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-63925:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-63925:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-63925:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/63925
Category: accuracy
Buggy: caa6a62b7cf16a8515b14cf936afcefa8ab495a7
Fixed: 39ffc454f49d79d6cd7bef2181d7cd207ec9adeb

Test: python -I -m pytest --pyargs pandas.tests.apply.test_series_apply -k test_apply_nullable_integer_precision -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-63925:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

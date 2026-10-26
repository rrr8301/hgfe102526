# pandas-68315

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68315:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68315:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68315:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68315:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68315
Category: correctness
Buggy: 7d79fc4499441ace8aa7bb79c385b8cfb642383d
Fixed: a77f7cd865b78bb728d410d946a5ec20e9d8511c

Test: python -I -m pytest --pyargs pandas.tests.dtypes.cast.test_downcast -k test_downcast_float16_no_overflow_warning -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68315:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

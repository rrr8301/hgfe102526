# pandas-65168

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-65168:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-65168:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-65168:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-65168:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/65168
Category: correctness
Buggy: f8168b516e0a6eccd8d29120f1f2171d8a0d91b7
Fixed: a30de33ca0f2536e5c3b8a32bab0d4802340dbe7

Test: python -I -m pytest --pyargs pandas.tests.scalar.timedelta.test_constructors -k test_construct_from_kwargs_non_nano -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-65168:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

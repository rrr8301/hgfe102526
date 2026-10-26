# pandas-66227

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66227:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66227:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66227:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66227:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66227
Category: correctness
Buggy: 6850d49c8de29df8f048f7b90fcf897464fff0f4
Fixed: 263e401f367e5a9f537c9257675daa03a300fff3

Test: python -I -m pytest --pyargs pandas.tests.frame.test_reductions -k test_sum_timedelta64_overflow_raises -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66227:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

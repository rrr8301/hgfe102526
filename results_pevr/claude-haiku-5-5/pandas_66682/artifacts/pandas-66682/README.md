# pandas-66682

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66682:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66682:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66682:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66682:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66682
Category: correctness
Buggy: 2e3e0ba45333641fa86dd6fee2559cfe946328e7
Fixed: 4da652d8d2a6840c74f9e744b361abc9a0418e0a

Test: python -I -m pytest --pyargs pandas.tests.arithmetic.test_period -k test_pi_sub_pi_count_on_nat_sentinel -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66682:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

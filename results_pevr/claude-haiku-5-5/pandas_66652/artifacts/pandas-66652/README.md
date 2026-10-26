# pandas-66652

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66652:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66652:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66652:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66652:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66652
Category: correctness
Buggy: d8659d45c49d09f2f207b002d172e1af1a7555a7
Fixed: 3ac4ba16373ec2d25839b49f425a26864e86f0a1

Test: python -I -m pytest --pyargs pandas.tests.tseries.offsets.test_week -k test_week_apply_array_lands_on_nat_sentinel -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66652:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

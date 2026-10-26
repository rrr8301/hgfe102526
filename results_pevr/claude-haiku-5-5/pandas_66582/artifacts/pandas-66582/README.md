# pandas-66582

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66582:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66582:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66582:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66582:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66582
Category: accuracy
Buggy: 92d805f913febf5ece5a91a96d2a54ef8b7f0c7d
Fixed: eac34231b720d3f9fa2130bc87dcfc607f2138c2

Test: python -I -m pytest --pyargs pandas.tests.frame.test_reductions -k test_sum_timedelta64_exact -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66582:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

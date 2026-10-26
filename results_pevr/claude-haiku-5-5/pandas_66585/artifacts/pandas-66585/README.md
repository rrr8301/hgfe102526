# pandas-66585

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66585:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66585:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66585:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66585:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66585
Category: accuracy
Buggy: 12d8a044697ff24147e804e2396eafe10747840c
Fixed: 561399f2b1cef7a9a4e7b1a68fd5a73b4f944cd9

Test: python -I -m pytest --pyargs pandas.tests.scalar.timedelta.test_arithmetic -k test_td_div_integer_exact -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66585:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

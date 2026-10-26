# pandas-62542

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-62542:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-62542:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-62542:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-62542:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/62542
Category: accuracy
Buggy: d8b3ff3a524cafc8873263c9fb63d6f5b84fbb52
Fixed: db31f6a38353a311cc471eb98506470b39c676d8

Test: python -I -m pytest --pyargs pandas.tests.io.parser.common.test_float -k test_small_int_followed_by_float -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-62542:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

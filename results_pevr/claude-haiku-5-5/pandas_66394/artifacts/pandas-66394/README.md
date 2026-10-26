# pandas-66394

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66394:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66394:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66394:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66394:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66394
Category: correctness
Buggy: 389c06f9d7d9a5695552e2f152f7155fefcc3a69
Fixed: 76bac9e32eeee63f003ffecd431e8463a35e9b3c

Test: python -I -m pytest --pyargs pandas.tests.dtypes.cast.test_downcast -k test_downcast_float_out_of_integer_range -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66394:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# pandas-63609

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-63609:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-63609:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-63609:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-63609:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/63609
Category: correctness
Buggy: e49e355cad462cb7b50f794bd2828d1c13938a1b
Fixed: 17b66cc0ad8c26292bfee55e019e1ea38ef6177b

Test: python -I -m pytest --pyargs pandas.tests.scalar.period.test_period -k test_construction_from_min_timestamp -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-63609:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

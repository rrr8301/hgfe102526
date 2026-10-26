# pandas-66520

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66520:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66520:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66520:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66520:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66520
Category: correctness
Buggy: 982854070758cd2015fc9e64395684546b1c5444
Fixed: 082e77392bdbb7683fa29a2dfd335fa5c7f623f0

Test: python -I -m pytest --pyargs pandas.tests.io.parser.test_parse_dates -k test_parse_dates_c_fastpath_nat_sentinel -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66520:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

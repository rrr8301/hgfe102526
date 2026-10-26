# pandas-64796

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-64796:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-64796:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-64796:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-64796:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/64796
Category: special_value
Buggy: 0563919a033e629c54cef19b1777c19fd75f0124
Fixed: afd515e7fb376206efc99b003a861d02bc733745

Test: python -I -m pytest --pyargs pandas.tests.indexes.datetimes.test_indexing -k test_get_indexer_nat_target -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-64796:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

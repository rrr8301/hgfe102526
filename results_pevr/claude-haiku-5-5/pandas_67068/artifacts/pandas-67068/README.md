# pandas-67068

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-67068:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-67068:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-67068:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-67068:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/67068
Category: special_value
Buggy: af290b6ae3e7b255663f46350eb5e75d32dce752
Fixed: e34122aa1b84ce133fd973c83ecac014d15dcf01

Test: python -I -m pytest --pyargs pandas.tests.test_nanops -k test_nanops_reductions_dont_skip_nan_with_mask -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-67068:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

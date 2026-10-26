# pandas-66183

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66183:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66183:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66183:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66183:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66183
Category: special_value
Buggy: da4c75bc72f9270c47671f9a7180e506d163ba19
Fixed: 06200bf22171aec8c91bc6941fa30a9945589510

Test: python -I -m pytest --pyargs pandas.tests.groupby.methods.test_quantile -k test_groupby_quantile_nonnull_nan_masked -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66183:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

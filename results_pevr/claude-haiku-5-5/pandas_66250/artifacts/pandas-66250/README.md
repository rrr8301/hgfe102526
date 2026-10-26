# pandas-66250

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66250:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66250:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66250:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66250:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66250
Category: accuracy
Buggy: 633e72f17a6e94dd1d350320ab10caf0c25d5300
Fixed: 8de38cfcaa2538414f951a81cfce958e6f1a1165

Test: python -I -m pytest --pyargs pandas.tests.test_nanops -k test_nanargminmax_uint64_with_mask -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66250:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

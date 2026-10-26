# pandas-62405

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-62405:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-62405:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-62405:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-62405:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/62405
Category: accuracy
Buggy: dab2f73b0b61052e2a3481cae0cbaec75c9ad244
Fixed: 4e92c631cd8248cb3e76223259a8cca3060d80ce

Test: python -I -m pytest --pyargs pandas.tests.test_nanops -k test_low_variance -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-62405:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

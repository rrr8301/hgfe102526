# pandas-66180

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66180:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66180:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66180:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66180:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66180
Category: correctness
Buggy: 15b1dd913e3df72854955936b0d89c7f95648a9a
Fixed: cdd3ab99431e3a2e6706be5c962c6e1c780e2f9f

Test: python -I -m pytest --pyargs pandas.tests.arrays.timedeltas.test_constructors -k test_from_sequence_numeric_honors_unit -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66180:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# statsmodels-10013

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-10013:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-10013:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-10013:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-10013:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/10013
Category: accuracy
Buggy: d3cb403cb5d0681c73e283d08db0ea258403186b
Fixed: 57d01f5b3893f2e3693df3af5b00a023abeaf6fa

Test: python -I -m pytest --pyargs statsmodels.stats.tests.test_multi -k test_multipletests_empty -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-10013:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

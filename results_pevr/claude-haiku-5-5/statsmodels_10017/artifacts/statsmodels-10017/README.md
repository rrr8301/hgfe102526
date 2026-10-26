# statsmodels-10017

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-10017:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-10017:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-10017:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-10017:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/10017
Category: correctness
Buggy: da83c3b8e37fb382b06f8534534b9be88f42c7a4
Fixed: 2edccacdb602db748963e36766d57f722968a69e

Test: python -I -m pytest --pyargs statsmodels.tsa.tests.test_stattools -k test_qstat_drop -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-10017:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

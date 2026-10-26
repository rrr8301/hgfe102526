# statsmodels-10230

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-10230:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-10230:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-10230:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-10230:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/10230
Category: correctness
Buggy: e5a2af4cbf14576f14c5d3a9ad2b263fc35b10a9
Fixed: e7f76fec5421daa85e697bb2afbbe1cd220fd65f

Test: python -I -m pytest --pyargs statsmodels.tsa.holtwinters.tests.test_holtwinters -k test_seasonal_forecast_uses_final_season -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-10230:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

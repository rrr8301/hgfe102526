# statsmodels-9979

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-9979:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-9979:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-9979:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-9979:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/9979
Category: correctness
Buggy: 51e6bab53568f3aad791b71c4cd47f8435e95206
Fixed: a149402577e87dd23be27737b53123ab7d02d6d8

Test: python -I -m pytest --pyargs statsmodels.tsa.statespace.tests.test_univariate -k test_nondiagonal_obs_cov_smoothed_decomposition -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-9979:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

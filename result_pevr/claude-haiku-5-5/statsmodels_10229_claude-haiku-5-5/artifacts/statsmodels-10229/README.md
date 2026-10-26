# statsmodels-10229

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-10229:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-10229:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-10229:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-10229:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/10229
Category: correctness
Buggy: 522283ef64247f8d465b766684f01631c34b1561
Fixed: a0e465d212006e3de3af37958444d1f3b2d85ccb

Test: python -I -m pytest --pyargs statsmodels.discrete.tests.test_discrete -k test_probit_loglikeobs_extreme_linpred -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-10229:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

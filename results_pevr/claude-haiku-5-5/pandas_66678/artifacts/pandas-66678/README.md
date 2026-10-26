# pandas-66678

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66678:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66678:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66678:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66678:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66678
Category: accuracy
Buggy: 6663b2123b9a90c1d77212d7928c2a915fc69c38
Fixed: 18e2ea6f3344b064169b24618fc5e8480c288b56

Test: python -I -m pytest --pyargs pandas.tests.window.test_pairwise -k test_rolling_cov_corr_outlier_exit -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66678:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

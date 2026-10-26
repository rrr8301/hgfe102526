# scipy-21335

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21335:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21335:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21335:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21335:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21335
Category: accuracy
Buggy: 87ed0cab1a60f54d5c5223fbd8e99e5545f16204
Fixed: 1d50e6e6008b1f21aae5e35ff5fae656dd8f8b43

Test: python -I -m pytest --pyargs scipy.stats.tests.test_distributions -k test_cdf_sf -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21335:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

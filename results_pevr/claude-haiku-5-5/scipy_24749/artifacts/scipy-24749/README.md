# scipy-24749

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-24749:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-24749:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-24749:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-24749:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/24749
Category: correctness
Buggy: d2a1c1ce1f2e69b5a3fb23946e43987b4cd0ac93
Fixed: dd4723ba8bedbe08dbd6d93ccd634c82ba56d9ae

Test: python -I -m pytest --pyargs scipy.signal.tests.test_fir_filter_design -k test_nyquist -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-24749:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# scipy-26044

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-26044:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-26044:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-26044:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-26044:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/26044
Category: convergence
Buggy: a804efa6ece58f62f5b4af667eec70234c160276
Fixed: 34d57a75ac3a7afa07266fb1e5b34cac19e9fd76

Test: python -I -m pytest --pyargs scipy.optimize.tests.test_trustregion -k test_trust_exact_rejects_nan_trial_point -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-26044:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

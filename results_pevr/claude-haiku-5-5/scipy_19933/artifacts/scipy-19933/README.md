# scipy-19933

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-19933:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-19933:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-19933:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-19933:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/19933
Category: special_value
Buggy: 44b39e1e740795aa8cd4d69e88d6c0acb116eeac
Fixed: a160e856a3f7684402ae0f4b9463417f75741e56

Test: python -I -m pytest --pyargs scipy.special.tests.test_cdflib -k test_stdtr_stdtrit_neg_inf -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-19933:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

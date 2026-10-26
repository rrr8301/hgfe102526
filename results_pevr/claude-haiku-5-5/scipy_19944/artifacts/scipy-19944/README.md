# scipy-19944

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-19944:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-19944:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-19944:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-19944:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/19944
Category: special_value
Buggy: adc9b901128ae7cb9b647947dcbc8ccd3ea9b1f5
Fixed: a1a3804582539a0e20708ab023345f9f6f670ea1

Test: python -I -m pytest --pyargs scipy.special.tests.test_cdflib -k test_bdtrik_nbdtrik_inf -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-19944:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

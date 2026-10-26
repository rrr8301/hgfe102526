# astropy-20240

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-20240:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-20240:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-20240:latest fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-20240:latest metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/20240
Category: accuracy
Buggy: e7821563dc267b7e9f53eb4f5e3d066b257e2a92
Fixed: cb3c511e2e6eae23618ca7f38ae1b55f1cf1a615

Test: python -I -m pytest --pyargs astropy.table.tests.test_df -k test_nullable_int_no_float_intermediate -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-20240:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

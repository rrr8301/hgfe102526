# astropy-15830

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-15830:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-15830:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-15830:latest fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-15830:latest metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/15830
Category: special_value
Buggy: c22af5c68f284e984ec9d42557144fd1055828da
Fixed: f9a61f1aab35861745431d9b21598d879e14ac84

Test: python -I -m pytest --pyargs astropy.time.tests.test_delta -k test_time_delta_comp_nan_quantity -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-15830:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# numpy-31199

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-31199:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-31199:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-31199:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-31199:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/31199
Category: special_value
Buggy: 1388e933f82b7c14e89c67c1e0be1800c3b88b07
Fixed: 5b4c8933532baf7892b2cb2269635fcef3b43f99

Test: python -I -m pytest --pyargs numpy.f2py.tests.test_capi_maps -k test_complex_long_double_capi_map -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-31199:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

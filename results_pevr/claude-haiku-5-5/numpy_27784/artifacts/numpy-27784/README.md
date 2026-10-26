# numpy-27784

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-27784:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-27784:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-27784:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-27784:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/27784
Category: accuracy
Buggy: 84c33941759bcfe5f2ad0cb3bbc462b8f9e8bfc8
Fixed: 1356272f0e2d421b045ce7ecf80b6e48148f0733

Test: python -I -m pytest --pyargs numpy.lib.tests.test_function_base -k test_float_dtypes -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-27784:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

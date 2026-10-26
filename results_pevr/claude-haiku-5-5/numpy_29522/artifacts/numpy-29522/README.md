# numpy-29522

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-29522:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-29522:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-29522:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-29522:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/29522
Category: accuracy
Buggy: f6c2bb97146adb8f8e1a65cd6fccd2a12976ba40
Fixed: 0653d084c7182480d317097099d5ad9509a766bc

Test: python -I -m pytest --pyargs numpy.random.tests.test_generator_mt19937 -k test_p_extremely_small -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-29522:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

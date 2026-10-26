# scipy-20805

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20805:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20805:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20805:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20805:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20805
Category: correctness
Buggy: dc9cbc03c0393fcd50b2f496216ad871490a5cf0
Fixed: 64601941a19ab8d765d8f64e33ec95d58692f457

Test: python -I -m pytest --pyargs scipy.special.tests.test_hypergeometric -k test_complex_z -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20805:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

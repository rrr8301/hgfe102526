# numpy-27148

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-27148:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-27148:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-27148:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-27148:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/27148
Category: correctness
Buggy: 7e5dc822ef2231d5919c2514fb8c8a749b344857
Fixed: 251f7e11fc5316cfd75e223e6b78d5298ef8e38b

Test: python -I -m pytest --pyargs numpy.lib.tests.test_histograms -k test_small_value_range -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-27148:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

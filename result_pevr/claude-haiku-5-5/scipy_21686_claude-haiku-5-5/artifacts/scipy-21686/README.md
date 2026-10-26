# scipy-21686

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21686:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21686:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21686:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21686:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21686
Category: correctness
Buggy: 94b14c8efcfa9c3f6455dabfb26ea231afac9339
Fixed: fec5d2012691729c0b49bb26277464891ac4f189

Test: python -I -m pytest --pyargs scipy.signal.tests.test_signaltools -k test_gh_15620 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21686:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

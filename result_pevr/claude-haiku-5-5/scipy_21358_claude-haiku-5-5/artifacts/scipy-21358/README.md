# scipy-21358

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21358:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21358:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21358:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21358:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21358
Category: accuracy
Buggy: ed184ffc199495393ca48d46b021867fe21dc837
Fixed: 701cd9afec92a24eaa09225f0abc5036910fecad

Test: python -I -m pytest --pyargs scipy.stats.tests.test_distributions -k test_sf -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21358:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

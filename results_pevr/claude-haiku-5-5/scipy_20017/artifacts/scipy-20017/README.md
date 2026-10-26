# scipy-20017

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20017:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20017:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20017:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20017:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20017
Category: correctness
Buggy: e7d71d6d68271b47617286bc30d5926869d73227
Fixed: 673a15da1366012f1e701f9c496fa465c2a16e50

Test: python -I -m pytest --pyargs scipy.stats.tests.test_distributions -k test_moments_stats -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20017:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

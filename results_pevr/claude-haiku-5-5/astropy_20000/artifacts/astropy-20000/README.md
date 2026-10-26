# astropy-20000

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-20000:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-20000:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-20000:latest fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-20000:latest metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/20000
Category: correctness
Buggy: 0d03eb21bfdb2f6ca66ab37c9af2149e633e1d1f
Fixed: 8f2baae6e29cef1a53a4a98912208ca316e58bc7

Test: python -I -m pytest --pyargs astropy.wcs.wcsapi.tests.test_fitswcs -k test_components_and_classes_cache -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-20000:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

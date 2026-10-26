# scikit-image-7473

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-image-7473:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-image-7473:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-image-7473:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-image-7473:latest metadata   # the manifest

Bug: https://github.com/scikit-image/scikit-image/pull/7473
Category: correctness
Buggy: e5e1742bb411f02dc294302aa60d5073d3c8fcb9
Fixed: 6baff20f0fe4c3bee859447f77f0014ec25f820b

Test: python -I -m pytest --pyargs skimage.draw.tests.test_draw3d -k test_ellipsoid_stats -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-image-7473:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

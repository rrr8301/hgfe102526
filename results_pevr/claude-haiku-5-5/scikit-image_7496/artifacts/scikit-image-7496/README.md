# scikit-image-7496

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-image-7496:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-image-7496:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-image-7496:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-image-7496:latest metadata   # the manifest

Bug: https://github.com/scikit-image/scikit-image/pull/7496
Category: accuracy
Buggy: f1892afb76e60b6fe41cf0c73be97f918581aa33
Fixed: 137dbfb5f1e597cf4cb069486319d7defdd1077e

Test: python -I -m pytest --pyargs skimage.measure.tests.test_fit -k test_ransac_dynamic_max_trials_clipping -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-image-7496:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

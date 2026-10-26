# pandas-68469

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-68469:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-68469:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-68469:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-68469:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/68469
Category: accuracy
Buggy: 1087a91a531b17a2ce37160cdd09f658f3991ac7
Fixed: 32b3ba3573f72a1e39539f4f26ca11cff324c6f1

Test: python -I -m pytest --pyargs pandas.tests.arrays.sparse.test_indexing -k test_take_fill_preserves_subtype -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-68469:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

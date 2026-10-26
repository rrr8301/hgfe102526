# scikit-learn-32351

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-32351:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-32351:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-32351:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-32351:latest metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/32351
Category: correctness
Buggy: b1b01a1611e1f5af939e12e070e8bfad17ce25b2
Fixed: 8aceace67fa64d898f25726d61f22ca4f5a06359

Test: python -I -m pytest --pyargs sklearn.tree.tests.test_tree -k test_splitting_with_missing_values -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-32351:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

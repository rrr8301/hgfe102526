# scikit-learn-28365

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-28365:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-28365:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-28365:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-28365:latest metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/28365
Category: correctness
Buggy: 9846576508423f5bdb9e5936f03b005c081b5a65
Fixed: 29b14aea8b4d3ab9f63dd2b1f64ab0b10e6575ce

Test: python -I -m pytest --pyargs sklearn.impute.tests.test_impute -k test_simple_imputer_constant_fill_value_casting -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-28365:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

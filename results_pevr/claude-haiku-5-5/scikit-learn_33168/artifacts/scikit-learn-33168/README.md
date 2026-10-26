# scikit-learn-33168

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-33168:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-33168:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-33168:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-33168:latest metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/33168
Category: correctness
Buggy: 41f1f34791bf72d40e70462d92ac50469b38192e
Fixed: 839d1445e8bf5a3a4f8b31e017890bbd421bf76b

Test: python -I -m pytest --pyargs sklearn.linear_model.tests.test_base -k test_predict_proba_lr_large_values -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-33168:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

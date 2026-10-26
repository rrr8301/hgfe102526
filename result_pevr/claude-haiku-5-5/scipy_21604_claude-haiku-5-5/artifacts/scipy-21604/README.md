# scipy-21604

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21604:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21604:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21604:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21604:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21604
Category: correctness
Buggy: 8ca4ace96cff522024b84e227b24b4324491faa1
Fixed: bd853d5e8c08d942077d9f6da6697b77081b92c0

Test: python -I -m pytest --pyargs scipy.special.tests.test_orthogonal -k test_n_zero_gh8888 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21604:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

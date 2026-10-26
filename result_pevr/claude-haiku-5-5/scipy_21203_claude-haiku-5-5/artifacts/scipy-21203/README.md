# scipy-21203

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21203:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21203:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21203:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21203:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21203
Category: correctness
Buggy: 7f6379f7d929ca974067fb7b43324cbfaa6ca16d
Fixed: fdf15ba134fcfeaba52cf23d9903d43b3da04344

Test: python -I -m pytest --pyargs scipy.optimize._trustregion_constr.tests.test_nested_minimize -k test_gh21193 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21203:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

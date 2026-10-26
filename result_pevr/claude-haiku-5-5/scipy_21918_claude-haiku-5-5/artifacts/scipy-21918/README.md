# scipy-21918

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21918:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21918:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21918:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21918:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21918
Category: special_value
Buggy: 9285a8bc63edf6d65968202553ba1e00e8eda330
Fixed: 85bbb867b29227a01eb171eeb258a04322c6cc26

Test: python -I -m pytest --pyargs scipy.optimize.tests.test_minpack -k test_curvefit_omitnan -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21918:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

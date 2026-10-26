# statsmodels-10106

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-10106:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-10106:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-10106:latest fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-10106:latest metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/10106
Category: correctness
Buggy: 1e750ab0e99cb444bf2d15134598909c11f47a63
Fixed: b4737886f1f3e4d3291279e21490bd52ecae1e71

Test: python -I -m pytest --pyargs statsmodels.tools.tests.test_tools -k test_matrix_rank_column_order -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-10106:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

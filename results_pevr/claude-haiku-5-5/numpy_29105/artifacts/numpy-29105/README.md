# numpy-29105

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-29105:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-29105:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-29105:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-29105:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/29105
Category: correctness
Buggy: f39abd4ffbca44f01eec353115ffd2d7ad296fb2
Fixed: 884aec9750f0e42d563792678b2454ea86218f69

Test: python -I -m pytest --pyargs numpy.lib.tests.test_function_base -k test_percentile_gh_29003 -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-29105:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

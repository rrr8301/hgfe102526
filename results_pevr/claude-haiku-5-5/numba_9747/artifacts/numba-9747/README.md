# numba-9747

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numba-9747:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numba-9747:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numba-9747:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numba-9747:latest metadata   # the manifest

Bug: https://github.com/numba/numba/pull/9747
Category: correctness
Buggy: 30c17f24486ccfb63c2f46c8c8b69db0c9afd889
Fixed: 670ea3e5950d16c808da7044dcc55077d62d9fa3

Test: python -I -m pytest --pyargs numba.tests.test_np_randomgen -k test_binomial_specific_issues -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numba-9747:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

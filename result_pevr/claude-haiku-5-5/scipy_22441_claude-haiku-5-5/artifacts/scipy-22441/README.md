# scipy-22441

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-22441:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-22441:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-22441:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-22441:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/22441
Category: special_value
Buggy: fee31bf8964d351694e98e42ad663c9e64f16be3
Fixed: 6042deb5cbeef279a1466caae50dc35c5173ef28

Test: python -I -m pytest --pyargs scipy.special.tests.test_basic -k test_chi2_v_nan -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-22441:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

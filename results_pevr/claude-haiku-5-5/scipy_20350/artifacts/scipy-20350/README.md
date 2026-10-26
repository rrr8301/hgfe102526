# scipy-20350

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20350:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20350:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20350:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20350:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20350
Category: convergence
Buggy: 9d6aac2e67218ab9252fd421ef72d7bc84f8e7a5
Fixed: cef67bc46dc88633722575b91fab581e3aba4424

Test: python -I -m pytest --pyargs scipy.optimize.tests.test_linesearch -k test_scalar_search_wolfe2_with_low_amax -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20350:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

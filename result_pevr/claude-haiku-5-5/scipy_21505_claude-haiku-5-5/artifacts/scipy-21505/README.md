# scipy-21505

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-21505:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-21505:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-21505:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-21505:latest metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/21505
Category: accuracy
Buggy: 28709e534336cc8b898c028bb67903356bed2c3d
Fixed: cc9a0a87f3f65d9ec63f5e5bf23f8f6c4606919b

Test: python -I -m pytest --pyargs scipy.special.tests.test_cdflib -k test_ncfdtr -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-21505:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# astropy-20229

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-20229:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-20229:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-20229:latest fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-20229:latest metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/20229
Category: accuracy
Buggy: bdd570d53507583a7c58945ab58199e4be861ba5
Fixed: 2d636caf631632f9a0ee313b72bd82bf7f95f09c

Test: python -I -m pytest --pyargs astropy.coordinates.tests.test_formatting -k test_sexagesimal_seconds_round_at_half_a_unit -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-20229:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# numpy-28043

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-28043:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-28043:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-28043:latest fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-28043:latest metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/28043
Category: accuracy
Buggy: 031a8906a576d98727196b708d1de15452286d1d
Fixed: 9aa5cda4c502487fc69717507f6a9936b420365f

Test: python -I -m pytest --pyargs numpy._core.tests.test_einsum -k test_einsum_chunking_precision -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-28043:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

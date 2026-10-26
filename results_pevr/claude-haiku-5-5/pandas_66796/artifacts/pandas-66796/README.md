# pandas-66796

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-66796:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-66796:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-66796:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-66796:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/66796
Category: correctness
Buggy: 14350fa162178a10d91ae90c46c4d6bc7dead1bc
Fixed: e443dab23d03b409f5fac3a9279aba7849538ee9

Test: python -I -m pytest --pyargs pandas.tests.io.json.test_pandas -k test_to_json_object_dtype_unsigned_scalar -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-66796:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

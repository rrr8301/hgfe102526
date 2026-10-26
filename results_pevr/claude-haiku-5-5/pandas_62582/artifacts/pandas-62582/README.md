# pandas-62582

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-62582:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-62582:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-62582:latest fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-62582:latest metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/62582
Category: correctness
Buggy: 25607882c53f45c8a3eb9bdf17615e4db9f3abc8
Fixed: 21130b13c5dd97508eadb578a4fae3aa2a1e77b1

Test: python -m pytest pandas/tests/io/parser/common/test_ints.py -k test_outside_int64_uint64_range -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-62582:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

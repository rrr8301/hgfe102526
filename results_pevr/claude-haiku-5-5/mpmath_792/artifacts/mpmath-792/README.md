# mpmath-792

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/mpmath-792:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/mpmath-792:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/mpmath-792:latest fixed      # the same test on the fixed revision
    docker run numbugbench/mpmath-792:latest metadata   # the manifest

Bug: https://github.com/mpmath/mpmath/pull/792
Category: special_value
Buggy: ff73d54d2115597f2516843ecb15a8a169ca691b
Fixed: 08cbde4bfbb2a6079644021fc51b3d179cce1a5e

Test: python -m pytest mpmath/tests/test_functions2.py -k test_hyper_1f1 -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/mpmath-792:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# mpmath-1101

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/mpmath-1101:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/mpmath-1101:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/mpmath-1101:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/mpmath-1101:eradyn-agent metadata   # the manifest

Bug: https://github.com/mpmath/mpmath/pull/1101
Category: convergence
Buggy: e45f046a02e3ec92a3ee38512253b37f89546cf8
Fixed: 29c496bf1419f74902bfbc597b28c01897af50b6

Test: python -m pytest mpmath/tests/test_functions2.py -k test_gegenbauer -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/mpmath-1101:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

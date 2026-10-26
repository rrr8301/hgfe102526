# statsmodels-9390

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-9390:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-9390:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-9390:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-9390:eradyn-agent metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/9390
Category: correctness
Buggy: d0da572b061faf1c691b471e6ad60257b0a3ccf2
Fixed: 9575cf4dee76f929e8492763ce3271243052c7b4

Test: python -m pytest statsmodels/tsa/ardl/tests/test_ardl.py -k test_resids_ardl_uecm -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-9390:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

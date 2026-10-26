# mpmath-839

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/mpmath-839:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/mpmath-839:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/mpmath-839:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/mpmath-839:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/mpmath/mpmath/pull/839
Category: correctness
Buggy: f04fa498d9473e50e652c18825e335af3da6ee96
Fixed: d5c8e0c6174ea006a12255252c598b6c8be7749c

Test: python -m pytest mpmath/tests/test_gammazeta.py -k test_issue_472 -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/mpmath-839:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# mpmath-969

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/mpmath-969:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/mpmath-969:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/mpmath-969:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/mpmath-969:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/mpmath/mpmath/pull/969
Category: accuracy
Buggy: 8a2bed58a5dfa8cc90b06bba373ecaade7129422
Fixed: 14854937118c1467ee93d6e0191f8066bcfa88a0

Test: python -m pytest mpmath/tests/test_basic_ops.py -k test_from_man_exp -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/mpmath-969:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

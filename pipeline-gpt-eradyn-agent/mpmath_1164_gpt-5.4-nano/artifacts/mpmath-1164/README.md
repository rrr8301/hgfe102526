# mpmath-1164

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/mpmath-1164:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/mpmath-1164:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/mpmath-1164:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/mpmath-1164:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/mpmath/mpmath/pull/1164
Category: accuracy
Buggy: 46e0959d4d5c0a193eb96adc5d79f2f31a0d0780
Fixed: 0fdd69316181af722f0d80e5adc7eb61a217543a

Test: python -m pytest mpmath/tests/test_convert.py -k test_to_str -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/mpmath-1164:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

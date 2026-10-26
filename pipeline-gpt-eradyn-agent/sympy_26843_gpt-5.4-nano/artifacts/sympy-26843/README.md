# sympy-26843

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-26843:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-26843:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-26843:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-26843:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/26843
Category: correctness
Buggy: 958cc95aebc35ced70d870586e68d0468684c1a0
Fixed: 32ca4ea603355547bb8cb379cb41544aec53b58c

Test: python -m pytest sympy/polys/tests/test_polytools.py -k test_cancel_modulus -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-26843:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

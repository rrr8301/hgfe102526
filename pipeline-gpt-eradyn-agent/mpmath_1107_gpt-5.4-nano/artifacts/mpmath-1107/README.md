# mpmath-1107

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/mpmath-1107:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/mpmath-1107:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/mpmath-1107:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/mpmath-1107:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/mpmath/mpmath/pull/1107
Category: accuracy
Buggy: 3f3d3ec9a3a39a33717249957f22ed46d127e6d5
Fixed: 60cf796714a8d7b9fe3cb091e76e68aa36144fc9

Test: cd /tmp && python -V && pytest --version && cd /workspace/buggy && pytest mpmath/tests/test_elliptic.py -k test_issue_1104 -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/mpmath-1107:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

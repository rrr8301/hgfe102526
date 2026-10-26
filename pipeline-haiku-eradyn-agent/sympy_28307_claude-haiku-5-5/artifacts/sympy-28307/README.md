# sympy-28307

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-28307:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-28307:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-28307:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-28307:eradyn-agent metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/28307
Category: correctness
Buggy: 7bf4a6bf35442378c37c7ace10b6dd10780be8d4
Fixed: bc3cbb7ba59ff192f111816aaa2f56188b296bb6

Test: python -m pytest sympy/core/tests/test_expr.py -k test_round -q -rs -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-28307:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# sympy-28565

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/sympy-28565:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/sympy-28565:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/sympy-28565:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/sympy-28565:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/sympy/sympy/pull/28565
Category: correctness
Buggy: 6f1468e51e690fece5976d20533708ae0f4e3278
Fixed: 90ef1a70459a6ba9785679da104cda45b430d471

Test: python -m pytest sympy/algebras/tests/test_quaternion.py -k test_issue_28556 -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/sympy-28565:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

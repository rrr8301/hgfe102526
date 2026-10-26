# jax-19936

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/jax-19936:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/jax-19936:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/jax-19936:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/jax-19936:eradyn-agent metadata   # the manifest

Bug: https://github.com/jax-ml/jax/pull/19936
Category: correctness
Buggy: e1e9de0e7b5f29840b4e63ea91e1077bb13f9ed9
Fixed: ae9d677b63a24e5fe340537a4256a43aef27b1f4

Test: python -m pytest tests/lax_numpy_test.py -k testIsClose -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/jax-19936:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

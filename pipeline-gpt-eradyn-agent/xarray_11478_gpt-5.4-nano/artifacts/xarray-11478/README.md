# xarray-11478

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/xarray-11478:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/xarray-11478:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/xarray-11478:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/xarray-11478:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/pydata/xarray/pull/11478
Category: special_value
Buggy: 5c1ff1cb2e3ce98906877c56fd267e6f9f886337
Fixed: 0c2d2b40c4f1738d417835c35201ea235f8d1f4c

Test: python -m pytest xarray/tests/test_computation.py -k test_polyval_timedelta_nat -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/xarray-11478:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

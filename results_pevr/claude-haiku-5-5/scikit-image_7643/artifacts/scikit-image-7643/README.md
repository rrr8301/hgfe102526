# scikit-image-7643

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-image-7643:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-image-7643:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-image-7643:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-image-7643:latest metadata   # the manifest

Bug: https://github.com/scikit-image/scikit-image/pull/7643
Category: accuracy
Buggy: bb7f08a4c802b4394504e295e587254a906c9a96
Fixed: 126455a9548e11da884f0967539f0a3f3ea8b1a9

Test: python -m pytest skimage/measure/tests/test_blur_effect.py -k test_blur_effect_uniform_input -q

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-image-7643:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# scikit-learn-33626

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scikit-learn-33626:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scikit-learn-33626:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/scikit-learn-33626:latest fixed      # the same test on the fixed revision
    docker run numbugbench/scikit-learn-33626:latest metadata   # the manifest

Bug: https://github.com/scikit-learn/scikit-learn/pull/33626
Category: correctness
Buggy: 6f8b95aa2234102acc3804fc8c7a3e6bd0506bfb
Fixed: 76e81962bc4c9e048b0e12e66c781f33c0706990

Test: python -I -m pytest --pyargs sklearn.tests.test_discriminant_analysis -k test_lda_solver_agreement -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scikit-learn-33626:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

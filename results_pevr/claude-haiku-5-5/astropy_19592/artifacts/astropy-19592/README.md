# astropy-19592

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-19592:latest validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-19592:latest buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-19592:latest fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-19592:latest metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/19592
Category: special_value
Buggy: 8697e1025b1e5c8b8e58b076aa4686114a9f4856
Fixed: 90aef04ce4fe740038383a5205e298fc0b482628

Test: python -I -m pytest --pyargs astropy.io.fits.hdu.compressed.tests.test_fitsio -k test_decompress_integers -q -rs --confcutdir=.venv -W ignore::pytest.PytestDeprecationWarning

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-19592:latest evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

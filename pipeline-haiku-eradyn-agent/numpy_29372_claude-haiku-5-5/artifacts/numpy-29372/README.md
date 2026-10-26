# numpy-29372

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-29372:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-29372:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-29372:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-29372:eradyn-agent metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/29372
Category: special_value
Buggy: bde097d548a1dddcd21084400a8fdddd6c0683d2
Fixed: 447a903b95f885760cf8833f2787f016a5dd1b30

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs numpy.lib.tests.test_arraysetops -k test_unique_axis0_equal_nan_on_1d_array -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest numpy/lib/tests/test_arraysetops.py -k test_unique_axis0_equal_nan_on_1d_array -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-29372:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

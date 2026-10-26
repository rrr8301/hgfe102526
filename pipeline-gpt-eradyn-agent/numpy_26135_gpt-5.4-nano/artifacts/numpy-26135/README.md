# numpy-26135

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-26135:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-26135:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-26135:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-26135:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/26135
Category: special_value
Buggy: e191a5f3889279e379cfacaf5ebe338a956985a8
Fixed: 970476737080a053d9be3d6f4479bb1b5cd17f19

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs numpy.ma.tests.test_core -k test_masked_array_underflow -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest numpy/ma/tests/test_core.py -k test_masked_array_underflow -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-26135:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

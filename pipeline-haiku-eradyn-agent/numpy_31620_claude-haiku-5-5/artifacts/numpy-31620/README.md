# numpy-31620

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-31620:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-31620:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-31620:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-31620:eradyn-agent metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/31620
Category: special_value
Buggy: b8853b27c1884002eefc55b8e3a87e7a1bac241f
Fixed: a2dea9776752caf57bb72ca4158c51b898009e62

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs numpy._core.tests.test_function_base -k test_inf_equal_endpoints -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest numpy/_core/tests/test_function_base.py -k test_inf_equal_endpoints -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-31620:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

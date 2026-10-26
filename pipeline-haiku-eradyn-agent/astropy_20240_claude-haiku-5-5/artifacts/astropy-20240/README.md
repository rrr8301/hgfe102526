# astropy-20240

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-20240:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-20240:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-20240:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-20240:eradyn-agent metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/20240
Category: accuracy
Buggy: e7821563dc267b7e9f53eb4f5e3d066b257e2a92
Fixed: cb3c511e2e6eae23618ca7f38ae1b55f1cf1a615

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs astropy.table.tests.test_df -k test_nullable_int_no_float_intermediate -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest astropy/table/tests/test_df.py -k test_nullable_int_no_float_intermediate -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-20240:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# astropy-17236

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-17236:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-17236:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-17236:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-17236:eradyn-agent metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/17236
Category: special_value
Buggy: 96b49c0ec08e024aec793bab658f2328d74a40cb
Fixed: af67a37b9e130f1b56136b0416c1d8bac39bc549

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs astropy.wcs.wcsapi.tests.test_high_level_api -k test_world_to_array_index_nan -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest astropy/wcs/wcsapi/tests/test_high_level_api.py -k test_world_to_array_index_nan -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-17236:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

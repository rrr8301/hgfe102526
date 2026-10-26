# pandas-67978

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-67978:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-67978:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-67978:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-67978:eradyn-agent metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/67978
Category: correctness
Buggy: b750c9ba4aa0fee7e4b497091b40ada62a245287
Fixed: 795cd84559d9147679bfe8461853569727a5ee69

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs pandas.tests.indexes.datetimes.methods.test_round -k test_round_freq_not_multiple_of_resolution -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest pandas/tests/indexes/datetimes/methods/test_round.py -k test_round_freq_not_multiple_of_resolution -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-67978:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

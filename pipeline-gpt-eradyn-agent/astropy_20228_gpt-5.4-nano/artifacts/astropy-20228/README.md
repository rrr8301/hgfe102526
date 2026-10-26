# astropy-20228

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/astropy-20228:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/astropy-20228:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/astropy-20228:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/astropy-20228:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/astropy/astropy/pull/20228
Category: correctness
Buggy: 6d9b3a797d8349ab1d0147c1b1ca50865d6e4217
Fixed: 6334a52f6980bd5042492e746ca30482c7e88daf

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs astropy.coordinates.tests.test_formatting -k test_sexagesimal_seconds_round_at_half_a_unit -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest astropy/coordinates/tests/test_formatting.py -k test_sexagesimal_seconds_round_at_half_a_unit -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/astropy-20228:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

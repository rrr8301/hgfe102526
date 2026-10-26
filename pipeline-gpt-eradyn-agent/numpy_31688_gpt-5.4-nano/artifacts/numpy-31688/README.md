# numpy-31688

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/numpy-31688:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/numpy-31688:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/numpy-31688:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/numpy-31688:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/numpy/numpy/pull/31688
Category: correctness
Buggy: 7db129300a754b6df58115f744d37fa3b9f0704f
Fixed: cd1cfb8ef69f07a045180295224cc2fcddf821a1

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs numpy._core.tests.test_datetime -k test_datetime64_item_int64_min_edge_case -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning -s 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest numpy/_core/tests/test_datetime.py -k test_datetime64_item_int64_min_edge_case -q -rs -W ignore::pytest.PytestDeprecationWarning -s; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/numpy-31688:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

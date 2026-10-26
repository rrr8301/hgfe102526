# scipy-20350

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20350:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20350:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20350:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20350:eradyn-agent metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20350
Category: convergence
Buggy: 9d6aac2e67218ab9252fd421ef72d7bc84f8e7a5
Fixed: cef67bc46dc88633722575b91fab581e3aba4424

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs scipy.optimize.tests.test_linesearch -k test_scalar_search_wolfe2_with_low_amax -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest scipy/optimize/tests/test_linesearch.py -k test_scalar_search_wolfe2_with_low_amax -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20350:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

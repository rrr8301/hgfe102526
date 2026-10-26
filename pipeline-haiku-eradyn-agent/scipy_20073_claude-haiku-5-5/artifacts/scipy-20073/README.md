# scipy-20073

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-20073:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-20073:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-20073:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-20073:eradyn-agent metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/20073
Category: accuracy
Buggy: d86bb5732c11f1e300eb449a2f04bbcb3117da69
Fixed: ebe1da89deaa4980f971f535c1735ab539e2db56

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs scipy.special.tests.test_boxcox -k test_boxcox_premature_overflow -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest scipy/special/tests/test_boxcox.py -k test_boxcox_premature_overflow -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-20073:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

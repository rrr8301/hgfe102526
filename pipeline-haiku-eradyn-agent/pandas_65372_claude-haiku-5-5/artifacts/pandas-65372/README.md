# pandas-65372

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/pandas-65372:eradyn-agent validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/pandas-65372:eradyn-agent buggy      # the developer test on the buggy revision
    docker run numbugbench/pandas-65372:eradyn-agent fixed      # the same test on the fixed revision
    docker run numbugbench/pandas-65372:eradyn-agent metadata   # the manifest

Bug: https://github.com/pandas-dev/pandas/pull/65372
Category: correctness
Buggy: c6a064f9ed2d2e26128760609a8000f26c5c9e2b
Fixed: d9026c2b638f9c43e3ebaaf61416f0a7f5b1e0e4

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs pandas.tests.groupby.test_reductions -k test_groupby_reductions_dont_skip_nan_with_mask -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest pandas/tests/groupby/test_reductions.py -k test_groupby_reductions_dont_skip_nan_with_mask -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/pandas-65372:eradyn-agent evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

# statsmodels-10232

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/statsmodels-10232:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/statsmodels-10232:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/statsmodels-10232:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/statsmodels-10232:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/statsmodels/statsmodels/pull/10232
Category: correctness
Buggy: e7f76fec5421daa85e697bb2afbbe1cd220fd65f
Fixed: 3beaeeb147df215616297de655a57f2508df19af

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs statsmodels.nonparametric.tests.test_kde -k test_fast_linbin_boundary_observations -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest statsmodels/nonparametric/tests/test_kde.py -k test_fast_linbin_boundary_observations -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/statsmodels-10232:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

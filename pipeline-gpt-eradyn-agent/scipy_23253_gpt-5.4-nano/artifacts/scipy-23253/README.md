# scipy-23253

A NumBugBench instance: one image, both revisions, the developer test, and the
environment the reproduction was validated in.

    docker run numbugbench/scipy-23253:eradyn-agent-gpt validate   # checks FAIL on buggy, PASS on fixed
    docker run numbugbench/scipy-23253:eradyn-agent-gpt buggy      # the developer test on the buggy revision
    docker run numbugbench/scipy-23253:eradyn-agent-gpt fixed      # the same test on the fixed revision
    docker run numbugbench/scipy-23253:eradyn-agent-gpt metadata   # the manifest

Bug: https://github.com/scipy/scipy/pull/23253
Category: correctness
Buggy: 6f1dac31620c69c63ba26a6f2216fc7616824ffc
Fixed: a6d7f373b5542f4aa1effced6038a8420c302943

Test: CC="$(python -c 'import sys; print(sys.prefix)' 2>/dev/null || echo .venv)"; __o=$(python -I -m pytest --pyargs scipy.interpolate.tests.test_rgi -k test_length_one_axis_all -q -rs --confcutdir="$CC" -W ignore::pytest.PytestDeprecationWarning 2>&1); __c=$?; if [ "$__c" -ne 0 ] && printf "%s" "$__o" | grep -qE "module or package not found|no tests ran|file or directory not found|ERROR: not found"; then python -I -m pytest scipy/interpolate/tests/test_rgi.py -k test_length_one_axis_all -q -rs -W ignore::pytest.PytestDeprecationWarning; else printf "%s\n" "$__o"; exit "$__c"; fi

`validate` separates "the test ran and failed" from "the test could not run".
An ImportError on the buggy revision reports `Test execution: ERROR` and
`INVALID` — it is never counted as FAIL.

## Evaluating a generated test (BRTG)

    docker run -v ./candidate.py:/benchmark/candidate/test.py numbugbench/scipy-23253:eradyn-agent-gpt evaluate-generated-test

prints `{"buggy": …, "fixed": …, "classification": …}` using the same oracle.

## Information leakage

This image is the EVALUATOR artifact and contains the ground truth — the fixed
revision, the test patch and the oracle metadata. Do not mount it as an agent's
workspace. Give the agent the buggy revision and the permitted context only.

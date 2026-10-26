# NumericPEVR

A numerical-behaviour-aware, evidence-guided PEVR agent. The architecture keeps
its shape — Plan, Execute, Verify, Replan — and changes what the agent consumes,
how it reads a result, and what it is permitted to alter.

It runs as a **driver** over `numagent.repro_session.run_session`, which already
accepts one, so `numagent/` is imported and never modified. The compact context
comes from `NumericSpecificMiner`.

```
NumericSpecificMiner  →  compact context + evidence refs
        │
   PLAN      short STRUCTURED plan: evidence_used, assumptions, actions
   EXECUTE   commands checked against the task boundary before they run
   VERIFY    V(T,E_B,E_F) with TYPED failures — exit code ≠ reproduction
   REPLAN    one diagnosis → one permitted change; evidence only on demand
        └──── repeat until VALID_F2P or budget exhausted
```

## The five modifications

| # | Modification | Where | What it does |
|---|---|---|---|
| 1 | evidence-guided planning | `plan.py` | `PLAN_SCHEMA` forces a short structured plan; `evidence_influence()` measures whether the mined context actually shaped it (cited vs grounded paths) |
| 2 | numerical-aware verification | `verify.py` | `classify()` → `F2P / P2P / F2F / P2F / SETUP_ERROR / INVALID`, with the buggy failure **typed** first; F2P requires a bug-exposing failure |
| 3 | failure-directed replanning | `diagnose.py` | `POLICY` maps each diagnosis to one instruction, the evidence it may request, and the moves it may **not** make |
| 4 | adaptive RepoMiner interaction | `evidence.py` | `retrieve_evidence(information_need, task, max_tokens)` with a budget, a duplicate check, and the mined view's isolation |
| 5 | task-specific boundaries | `boundaries.py` | HBEC may change the environment and not the developer test; BRTG may change the generated test and not the frozen environment |

## Verification is the important one

An ordinary PEVR reads a non-zero exit code as a failure. This one asks *why*
the test failed before it calls anything a reproduction:

```
numerical assertion on buggy, pass on fixed  -> F2P          valid=True
ImportError on buggy, pass on fixed          -> SETUP_ERROR  the environment, not the bug
unrelated TypeError on buggy, pass on fixed  -> INVALID      the transition looks like F2P
pass on both                                 -> P2P
fail on both                                 -> F2F
collection error                             -> SETUP_ERROR
```

Numerical values are recorded **only when the runner printed them**:
`{'observed_value': 0.123, 'expected_value': 0.125}` from `E assert 0.123 == 0.125`,
and nothing when the output is silent.

## Boundaries are enforced, not advised

```
HBEC NO  sed -i 's/rtol=1e-12/rtol=1e-3/' pandas/tests/test_x.py
HBEC OK  pip install 'numpy<2'
BRTG NO  pip install numpy==1.26
BRTG NO  cat pandas/tests/io/test_float.py
BRTG OK  python -c 'import pandas; print(pandas.read_csv(...))'
```

Every refused command is recorded in the trace with its reason, so "the agent
never relaxed a tolerance" is a measurement rather than a hope.

## Token behaviour

Each iteration's prompt is the compact context (~1–2k chars) plus a state block
of a few hundred — never the PR, the diff, the command history or the logs:

```json
{"iteration": 1, "task": "HBEC", "current_outcome": "P2P",
 "last_change": "pin numpy 1.21", "diagnosis": "bug_not_exposed",
 "attempted_versions": {"numpy": ["1.21", "1.22"]},
 "missing_evidence": "historical numpy dtype behaviour",
 "next_action": "Retrieve targeted evidence",
 "log_excerpt": "E  AssertionError\nMax absolute difference: 2.0\n1 failed in 0.4s"}
```

A 744-character pip+pytest log compacts to a 63-character excerpt that still
carries the diagnosis. `attempted_versions` stops the agent rediscovering a dead
end, and `retrieve_evidence` refuses a repeated need from its cache.

## Commands

```sh
# reproduce one instance against a mined context
python -m NumericPEVR run workspace/worag_patches/index_with_bugtype.json --pr 31360 \
    --context Strategy_numeric/numpy__numpy_31360.context.json \
    --task BRTG --provider openai --model gpt-5.4-nano \
    --max-iterations 4 --evidence-budget 2

# re-classify a finished numagent result with the numerical-aware validator
python -m NumericPEVR verify Extra/results/numpy_pr31360_gpt_nominer.json

# print the boundary / outcome / policy tables as the code holds them
python -m NumericPEVR explain
```

## What the result carries

`report.numeric_pevr` records `task`, `final`, `iterations`, `evidence_calls`,
`refusals`, the sequence of `verdicts` and `diagnoses`, and `evidence_influence`
per plan — the four configurations of the evaluation table (generic PEVR,
+ numerical miner, + numerical PEVR, + adaptive retrieval) can be compared on
F2P, tokens, iterations and cost from these fields alone.

## Status

Every deterministic part is implemented and exercised offline: the classifier on
six outcome pairs, value extraction, boundary enforcement on five commands, the
diagnosis policy, state compaction and the evidence cache. The loop itself has
not been run live — that needs a container, a mined context and an LLM.

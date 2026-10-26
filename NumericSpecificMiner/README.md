# NumericSpecificMiner

A numerical-aware, retrieval-augmented context miner for NumBugBench. It turns
one **already-confirmed** numerical bug into a compact, evidence-grounded,
provenance-carrying reproduction context, and from that into the strategy file
the reproducer consumes.

It does **not** decide whether a PR is a numerical bug. That is the Human–LLM
annotation's job; the annotated category is an *input* here and is what makes
retrieval specific.

```
PR link → crawl (PR, issues, discussion, diff, commits)
        → clone at the buggy commit
        → task view (what this task may see)
        → RAG: three questions
        → compact structured context  (|C| ≪ |D|)
        → strategy  →  numagent repro
                          │ fails
                          └→ diagnosis → targeted request → expand → strategy′
```

Nothing in `numbugminer/` is modified. Its GitHub fetch, clone, chunker, Chroma
store and provider clients are imported; every numerical-aware decision lives
here.

## Commands

```sh
# one instance, category from the annotation CSV, strategy included
python -m NumericSpecificMiner mine https://github.com/numpy/numpy/pull/31360 \
    --categories numbugbench_275_bugs_repo_pr_category.csv \
    --task brtg_no_diff --strategy \
    --provider openai --model gpt-5.4-nano --embed-provider ollama

# a whole annotated set (CSV: repo,pr-link,category)
python -m NumericSpecificMiner batch numbugbench_275_bugs_repo_pr_category.csv \
    --task no_diff --strategy --out-dir Strategy_numeric

# what the Strategy Generator actually reads
python -m NumericSpecificMiner show Strategy_numeric/numpy__numpy_31360.context.json --provenance

# adaptive expansion after a failed reproduction (DP7)
python -m NumericSpecificMiner expand Strategy_numeric/numpy__numpy_31360.context.json \
    --result Extra/results/numpy_pr31360_gpt_nominer.json
```

`--category` is required unless `--categories` covers the PR: the miner refuses
to proceed without one rather than inventing a classification.

## Where each design principle lives

| DP | Principle | Implementation |
|----|-----------|----------------|
| DP1 | numerical-semantics-aware retrieval | `questions.NUMERICAL_TERMS` appended to every query; `Question.retrieval_query()` |
| DP2 | one miner, two tasks | `views.HBEC` answers Q1 (environment → HBEC); the caller's BRTG view answers Q2/Q3 — one crawl, one clone, one chunking |
| DP3 | three questions, one category-conditioned | `questions.QUESTIONS`; `CATEGORY_TERMS` expands Q3's query, `CATEGORY_FOCUS` its instruction |
| DP4 | information-dense compression | `context.NumericalContext` — the three-section schema, nulls preserved; `compression()` measures \|C\|/\|D\|; `to_prompt()` is what the strategy reads |
| DP5 | provenance and task isolation | `context.Fact`; `views.filter_chunks` builds a **separate collection per view**, so withheld artifacts are absent from the index, not merely unranked; `provenance_ok()` re-checks every passage before it reaches a prompt |
| DP6 | era-aware dependencies | `era.ERA_TABLE`, `era_constraints()` labelling each dependency `explicit` / `era` / `unbounded`, and `NUMERICALLY_CONSEQUENTIAL` marking the ones that change results |
| DP7 | adaptive expansion | `expand.diagnose()` maps a finished `numagent` result to a request; `expand.expand()` answers it **in the same view**, appending to `context.expansions` |

## The isolation guarantee

`brtg_no_diff` indexes only `pr_body, pr_meta, issue, discussion, commit, web,
readme, config, source`. The source fix (`diff`) and the developer's test
(`test_diff`) never enter that collection, so no answer — and no later expansion
— can be grounded on them. `brtg_with_diff` admits the source fix and still
withholds the test. `hbec` sees everything, because the environment is not the
thing being predicted. Every context records the view it was mined in and what
that view withheld, and `show` prints the withholding to the reader.

Verified offline:

```
hbec            keeps [diff, issue, pr_body, readme, source, test_diff]   withholds []
brtg_no_diff    keeps [issue, pr_body, readme, source]                    withholds [diff, test_diff]
brtg_with_diff  keeps [diff, issue, pr_body, readme, source]              withholds [test_diff]
```

## Cost accounting

Each strategy record carries `usage` (the strategy call) and `miner_usage` (the
three RAG calls), and the wrapper's `usage_totals` sums both — so *tokens per
successful reproduction* can be computed from the strategies file and the run
results without a join:

```
tokens per success = (miner + strategy + PEVR tokens over all attempts)
                     / number of F2P successes
```

## The three emphases

The ablation this package answers — full-context prompting reproduced more bugs
than compact RAG, at a much higher token cost — says the compact context is not
too *short*, it is sometimes short of the **particular** facts a reproduction
turns on. Three mechanisms address that directly.

### 1. Structured evidence completeness (`completeness.py`)

A checklist of what a reproduction needs, scored per task and per category:

```
accuracy       required= 6  affected_api, observed_behavior, expected_behavior,
                            trigger_conditions, dtype, tolerance
special_value  required= 5  … dtype
correctness    required= 4
convergence    required= 4
```

`tolerance` is required for an Accuracy bug and not for a Correctness one,
because a 1-ULP defect is invisible at a default tolerance and that is exactly
how a compact context loses an instance. Each entry carries *why*, which becomes
the instruction a repair pass is given.

The miner assesses before writing anything and spends `--repair-calls` (default
1) closing REQUIRED gaps. Rehearsed offline: a context missing `tolerance`
scores 5/6, one targeted re-ask fills it, 6/6, one call spent.

A field that is null because the evidence is silent is reported differently from
one that is null because nothing asked — the per-question record in
`context.retrieval` says which questions ran.

### 2. Provenance isolation, enforced in three layers (`audit.py`)

| layer | mechanism | what it stops |
|---|---|---|
| structural | separate Chroma collection per view | a withheld artifact cannot be retrieved — it is not in the index |
| citation | `attribute()` drops a fact citing a withheld source | a model that saw something it should not lose that field |
| fingerprint | `leak_report()` compares the context's **values** against the withheld text | a leak that survived both, caught on the artifact itself |

Demonstrated offline: a context whose `expected_behavior` echoes the developer's
test is flagged (`ok: False`, with the shared n-grams named); the same context
phrased from the issue passes. A fact citing `test_diff` is dropped while one
citing `issue` is kept. Every mined context carries `retrieval.isolation`, so
the claim is auditable per instance rather than per pipeline.

### 3. Adaptive retrieval as a budgeted ladder (`escalate.py`)

```
rung 0  compact context                   default, ~1–2k chars
rung 1  targeted re-ask      +1 call      the diagnosed question, expanded query
rung 2  widened retrieval    +1 call      3× passages, neighbouring allowed sources
rung 3  full context         per instance the ablation's winner, as a fallback
```

`Budget(max_calls, max_rung)` bounds the climb and logs every rung's tokens and
cost, so the paper can report *tokens per successful reproduction* **per rung**
and show where the extra spend bought a reproduction. `climb()` is driven by a
reproduction failure when one exists and by completeness otherwise;
`full_context_block()` builds rung 3 from the same allow-list as the index, so
even the fallback cannot show a BRTG instance the developer's test.

## Status

The pipeline, the views, the era reconstruction, the context schema and the
diagnosis layer are implemented and exercised offline (`questions`, `views`,
`era`, `context`, `expand` all have deterministic behaviour that was checked
without a model). The three RAG calls and the strategy call have **not** been
run live yet; that needs one PR, four LLM calls and one clone.

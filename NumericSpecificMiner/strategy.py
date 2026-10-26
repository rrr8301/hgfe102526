"""Context -> the `*.strategies.json` the reproducer already consumes.

The Strategy Generator reads the compact context instead of the PR body, the
issue thread and the diff — that substitution is the whole of DP4. The output
shape is unchanged, so `numagent repro --strategies …` takes it as it stands.

Two strategies come off one context, because the benchmark asks two different
things of the same evidence:

    reproduction  build the historical environment and make the reported
                  numerical failure happen, then OBSERVE it — the bug is known,
                  the question is whether this environment still exhibits it
    brtg          write a test that FAILS on the buggy revision and PASSES on
                  the fixed one — the bug's behaviour must be turned into an
                  assertion with an oracle, which is a strictly harder ask

They are written separately rather than as one record with two sections: a
single prompt asked for both tends to collapse them, reporting the reproduction
script as if a passing run were the test. They are also stored in separate
files, because the harness keys strategies by PR number and two records for one
PR would silently shadow each other.
"""
from __future__ import annotations

import json
from datetime import date

from numbugminer import providers

from .context import NumericalContext

SYSTEM = (
    "You write a reproduction strategy for a confirmed numerical bug, from a "
    "compact evidence context another component extracted. Treat the context as "
    "the whole of what is known: a field that is null was NOT stated by the "
    "evidence, so plan around it rather than inventing it. Where the context "
    "distinguishes a documented dependency constraint from an era-inferred one, "
    "respect that distinction and say which you are relying on."
)

REPRODUCTION = "reproduction"
BRTG = "brtg"
KINDS = (REPRODUCTION, BRTG)

#: what each task asks of the same context. Appended to SYSTEM, so the two runs
#: differ in their objective and not merely in their wording.
BRIEF = {
    REPRODUCTION: (
        "TASK: REPRODUCTION. Reconstruct the environment in which this defect "
        "was observed, so that the project's own test for it FAILS on the buggy "
        "revision and PASSES on the fixed one. That transition is the whole of "
        "success: no value is checked separately, so do not plan around printing "
        "or inspecting numbers. Spend your detail where the transition is decided "
        "— the versions, build flags, compiler, backend, threading and platform "
        "that determine whether the behaviour exists at all, and the exact "
        "`test_command` that selects the relevant test. Where the evidence shows "
        "the behaviour needs a platform or toolchain you cannot assume, say so in "
        "`risks`: a defect that only appears under one architecture or sanitiser "
        "will not transition anywhere else, and naming that is more useful than a "
        "plan that quietly cannot work."
    ),
    BRTG: (
        "TASK: BUG-REVEALING TEST GENERATION. Write a test that FAILS on the "
        "buggy revision and PASSES on the fixed one. You have NOT been shown the "
        "developer's test or the fix, and you must not guess at their contents: "
        "derive the assertion from the reported behaviour. The oracle is the hard "
        "part — say whether correctness is exact equality, a tolerance, or an ULP "
        "bound, and where the reference value comes from. A tolerance looser than "
        "the defect's own error passes on both revisions and is a FAILED test, so "
        "prefer the strictest oracle the evidence supports.\n"
        "DIRECTION — the single most common way this task is failed. Every "
        "assertion in the test states the CORRECT behaviour, the behaviour the "
        "fixed revision produces. The test fails on the buggy revision as a "
        "CONSEQUENCE of that, because the buggy code does not produce it. Never "
        "write an assertion that the defect is present — never assert the wrong "
        "value, never assert 'the magnitude is huge here', never add a step that "
        "confirms the failure mode. Such an assertion holds on the buggy revision "
        "and breaks on the fixed one, which inverts the test: it then PASSES on "
        "buggy and FAILS on fixed, the exact opposite of what is wanted. "
        "`fails_on_buggy` is your PREDICTION about how the correct assertion will "
        "behave there — it is never something to encode as an assertion."
    ),
}

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "needs_build": {"type": "boolean"},
        "language": {"type": "string"},
        "environment": {"type": "object", "properties": {
            "python_version": {"type": ["string", "null"]},
            "pip_packages": {"type": "array", "items": {"type": "string"}},
            "system_packages": {"type": "array", "items": {"type": "string"}},
            "build_commands": {"type": "array", "items": {"type": "string"}},
            "pinned_because": {"type": "array", "items": {"type": "string"}},
        }, "required": ["pip_packages", "build_commands"]},
        "test_command": {"type": "string"},
        "reproducing_test": {"type": "string"},
        "success_criterion": {"type": "string"},
        "numerical_probe": {"type": "string"},
        "input_output_examples": {"type": "array", "items": {
            "type": "object",
            "properties": {"input": {"type": "string"},
                           "buggy_output": {"type": "string"},
                           "expected_output": {"type": "string"}},
            "required": ["input", "buggy_output", "expected_output"]}},
        "risks": {"type": "array", "items": {"type": "string"}},
        # BRTG-specific: the oracle is the thing a test adds over a script
        "oracle": {"type": "object", "properties": {
            "kind": {"type": "string",
                     "enum": ["exact", "tolerance", "ulp", "relative", "unknown"]},
            "tolerance": {"type": ["string", "null"]},
            "reference_source": {"type": ["string", "null"]},
            "why": {"type": ["string", "null"]},
        }},
        "fails_on_buggy": {"type": ["string", "null"],
                           "description": "what the assertion sees on the buggy "
                                          "revision, and why that makes it fail"},
        "passes_on_fixed": {"type": ["string", "null"]},
    },
    "required": ["summary", "needs_build", "test_command", "success_criterion",
                 "input_output_examples"],
}


def generate(ctx: NumericalContext, *, provider: str, model: str, host: str = "",
             test_patch: str = "", kind: str = REPRODUCTION) -> dict:
    """One strategy record for one task, in the shape the harness reads."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    system = f"{SYSTEM}\n\n{BRIEF[kind]}"
    tail = ("\n\nWrite the strategy as a JSON object per the schema. Quote "
            "concrete values in input_output_examples wherever the context "
            "supplies them; omit an example rather than invent its numbers.")
    if kind == REPRODUCTION:
        tail += (" `input_output_examples` are recorded for the benchmark but are "
                 "NOT graded on this task, so never trade away precision in the "
                 "environment or the test command to produce them. "
                 "`success_criterion` must be stated as the transition: what the "
                 "test does on the buggy revision, and what it does on the fixed "
                 "one.")
    if kind == BRTG:
        tail += (" Fill `oracle`, `fails_on_buggy` and `passes_on_fixed`: a test "
                 "whose behaviour on the two revisions you cannot state is not "
                 "yet a bug-revealing test.")
    user = (ctx.to_prompt()
            + (f"\n\nREPRODUCING TEST DIFF (the developer's test for this bug):\n"
               f"{test_patch[:12000]}" if test_patch else "")
            + tail)
    record = {
        "repo": ctx.repo, "pr_number": ctx.pr_number, "reference": ctx.reference,
        "buggy_commit": ctx.buggy_commit, "fixed_commit": ctx.fixed_commit,
        "bug_type": ctx.category,                 # ANNOTATED, carried through
        "task": kind,
        "evidence": f"numeric-specific-rag:{ctx.view}",
        "provider": provider,
        "model": model or providers.DEFAULT_CHAT_MODEL.get(provider, ""),
        "context_chars": len(ctx.to_prompt()),
        "withheld": ctx.withheld,
    }
    try:
        # 2000 was enough when the answer was the only thing generated. A model
        # that runs adaptive thinking spends part of this budget reasoning
        # before it writes the JSON, and the two share the cap: 22 of 570
        # Claude Haiku 5.5 strategies came back EMPTY at 2000, having spent it
        # thinking. The cap is an upper bound, not a reservation — a model that
        # does not need the room is not billed for it — so it is set high
        # enough for a thinking model to answer.
        res = providers.chat(provider, model, system, user, schema=SCHEMA, host=host,
                             max_tokens=6000)
        record["strategy"] = providers.extract_json(res.text)
        record["error"] = ""
        u = res.usage
        record["usage"] = {"model": model,
                           "input_tokens": getattr(u, "input_tokens", 0),
                           "output_tokens": getattr(u, "output_tokens", 0),
                           "cache_read_tokens": getattr(u, "cache_read_tokens", 0),
                           "cost_usd": getattr(u, "cost_usd", 0.0)}
    except Exception as e:                        # noqa: BLE001 — one instance
        record["strategy"], record["error"] = {}, f"{type(e).__name__}: {e}"
        record["usage"] = {}
    # what the miner spent getting here, kept beside what the strategy spent, so
    # "tokens per successful reproduction" can be computed without a join
    record["miner_usage"] = dict(ctx.usage)
    return record


#: one definition, in questions.py, so the question schemas and this one
#: cannot drift apart on the field Anthropic refuses to infer
from .questions import strict_objects as _strict_objects  # noqa: E402

_strict_objects(SCHEMA)


def strategies_path(out_dir, repo: str, kind: str = REPRODUCTION):
    """Where a kind's strategies live. The reproduction file keeps the name the
    harness is already pointed at; BRTG gets its own, so neither shadows the
    other when both are generated for the same PR."""
    from pathlib import Path

    prefix = repo.replace("/", "__")
    suffix = ".strategies.json" if kind == REPRODUCTION else f".{kind}.strategies.json"
    return Path(out_dir) / f"{prefix}_candidates{suffix}"


def write(records: list, outfile, *, provider: str, model: str, source: str = "") -> dict:
    """The wrapper `numagent repro --strategies` expects, merging on re-runs."""
    from pathlib import Path

    outfile = Path(outfile)
    outfile.parent.mkdir(parents=True, exist_ok=True)
    prior = []
    if outfile.is_file():
        try:
            raw = json.loads(outfile.read_text(encoding="utf-8"))
            prior = raw.get("strategies", []) if isinstance(raw, dict) else raw
        except Exception:                          # noqa: BLE001 — replaced
            prior = []
    fresh = {r.get("pr_number") for r in records}
    merged = [p for p in prior if p.get("pr_number") not in fresh] + records

    def tally(key: str) -> float:
        return sum((r.get("usage") or {}).get(key, 0) or 0 for r in merged) + \
               sum((r.get("miner_usage") or {}).get(key, 0) or 0 for r in merged)

    payload = {
        "input": source,
        "evidence": "numeric-specific-rag",
        "task": next((r.get("task") for r in merged if r.get("task")), REPRODUCTION),
        "generated": date.today().isoformat(),
        "provider": provider,
        "model": model or providers.DEFAULT_CHAT_MODEL.get(provider, ""),
        "n_strategies": len(merged),
        "usage_totals": {
            "input_tokens": tally("input_tokens"),
            "output_tokens": tally("output_tokens"),
            "cost_usd": round(tally("cost_usd"), 6),
            "llm_calls": sum((r.get("miner_usage") or {}).get("llm_calls", 0)
                             for r in merged) + len([r for r in merged if r.get("usage")]),
        },
        "strategies": merged,
    }
    outfile.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload

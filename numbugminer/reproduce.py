from __future__ import annotations

import json

from . import providers

_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "input_output_examples": {
            "type": "array",
            "description": "concrete input->output example(s) reported in the issue/PR/test",
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "input": {"type": "string", "description": "the exact call / expression / values fed in"},
                    "buggy_output": {"type": "string", "description": "what the buggy code produces (the reported wrong value)"},
                    "expected_output": {"type": "string", "description": "the correct value it should produce"},
                    "source": {"type": "string", "description": "where this example came from: issue | pr | test"},
                },
                "required": ["input", "buggy_output", "expected_output", "source"],
            },
        },
        "environment_setup": {"type": "array", "items": {"type": "string"}},
        "build_steps": {"type": "array", "items": {"type": "string"}},
        "reproduction_steps": {"type": "array", "items": {"type": "string"}},
        "test_command": {"type": "string"},
        "test_selector": {"type": "string"},
        "expected_buggy_behavior": {"type": "string"},
        "expected_fixed_behavior": {"type": "string"},
        "success_criterion": {"type": "string"},
        "risks_or_ambiguities": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["summary", "input_output_examples", "environment_setup", "build_steps",
                 "reproduction_steps", "test_command", "test_selector", "expected_buggy_behavior",
                 "expected_fixed_behavior", "success_criterion", "risks_or_ambiguities",
                 "confidence"],
}

_SYSTEM = (
    "You are a senior reproduction engineer writing a precise plan for an autonomous "
    "coding agent. The agent has the repository checked out at the BUGGY commit — the "
    "fix is NOT applied. Using ONLY the mined evidence provided, produce a concrete, "
    "ordered strategy to REPRODUCE the reported numerical bug and confirm it: set up "
    "the environment, build the project, run the reproducing test, and observe the "
    "expected failing behavior at this buggy revision. Prefer the exact dependency "
    "versions, build/test commands, and test selector from the evidence; do not invent "
    "commands the evidence does not support. Do NOT propose fixing the bug — only "
    "reproducing it. The success criterion is that the test FAILS on the buggy commit "
    "(and would PASS once fixed). Flag any ambiguities or missing information as risks. "
    "Because this is a NUMERICAL bug, the concrete numbers matter: populate "
    "input_output_examples with the specific example(s) reported in the issue/PR "
    "discussion and encoded in the reproducing test — the exact input (call, "
    "expression, or values), the WRONG value the buggy code produces, and the CORRECT "
    "value expected — and set `source` to issue, pr, or test. Copy values verbatim from "
    "the evidence; do NOT invent numbers. If the evidence states no concrete value, "
    "return an empty array. "
    "Return a single JSON object matching the schema."
)


#: The ablation arm's system prompt: same job, no mined evidence. It differs
#: from `_SYSTEM` in exactly one respect — what the model is allowed to lean on.
#: The mined prompt says "prefer the exact dependency versions, build/test
#: commands and test selector from the evidence"; there is no such evidence
#: here, so this one has to say what to do INSTEAD (read them off the README and
#: the diff, and say so in the risks when neither states them), or the model
#: quietly invents a plausible-looking build command and the arm measures
#: invention rather than absence.
_SYSTEM_PR_README = (
    "You are a senior reproduction engineer writing a precise plan for an autonomous "
    "coding agent. The agent has the repository checked out at the BUGGY commit — the "
    "fix is NOT applied. Your evidence is ONLY the pull request (its title, body and "
    "diff), the issues it links with their discussion, and the project's README: "
    "there is no mined analysis of this repository, no retrieval over its documents, "
    "no dependency survey and no CI transcript. Produce a concrete, ordered strategy "
    "to REPRODUCE the reported numerical bug and confirm it: set up the environment, "
    "build the project, run the reproducing test, and observe the expected failing "
    "behavior at this buggy revision.\n\n"
    "Take the build and test commands from the README where it states them, and the "
    "test selector from the test files the diff touches. Where neither the README nor "
    "the diff states something you need — a dependency version, a build flag, the "
    "exact runner — use the ordinary convention for this ecosystem and RECORD THAT "
    "CHOICE in risks_or_ambiguities. Do not present a guess as if it came from the "
    "project. Do NOT propose fixing the bug — only reproducing it. The success "
    "criterion is that the test FAILS on the buggy commit (and would PASS once "
    "fixed). Because this is a NUMERICAL bug, the concrete numbers matter: populate "
    "input_output_examples with the specific example(s) stated in the issue report or "
    "the PR description and encoded in the test assertions — the exact input, the "
    "WRONG value the buggy code produces, and the CORRECT value expected — with "
    "`source` set to issue, pr or test. The reported values are as often in the ISSUE "
    "as in the pull request: a bug report opens with the output someone actually got. "
    "Copy values verbatim; do NOT invent numbers, and return an empty array when "
    "nothing above states one. "
    "Return a single JSON object matching the schema."
)


#: Per-comment and total caps on the discussion block. A pandas PR can carry
#: forty comments of "LGTM", "green now" and merge chatter; the ones that matter
#: state a value or a version, and they say it early. Capping per comment rather
#: than taking the first N keeps a late "this still gives 1e-17 on macOS" in.
MAX_COMMENT_CHARS = 1_200
MAX_DISCUSSION_CHARS = 8_000


def _discussion(comments, limit: int = MAX_DISCUSSION_CHARS) -> str:
    """Comment bodies, each trimmed, until the block's budget runs out."""
    out, used = [], 0
    for text in comments:
        text = str(text or "").strip()
        if not text:
            continue
        text = text[:MAX_COMMENT_CHARS]
        if used + len(text) > limit:
            break
        out.append(text)
        used += len(text)
    return "\n---\n".join(out)


def build_prompt_pr_readme(record: dict, *, title: str = "", body: str = "",
                           diff: str = "", readme: str = "", issues=(), comments=(),
                           configs=(), max_diff: int = 20_000, max_readme: int = 12_000,
                           max_issue_body: int = 6_000, max_configs: int = 24_000) -> str:
    """Format the PR + issues + README evidence block for the no-RAG ablation arm.

    `record` is read for identity ONLY — repository, PR reference, the commit
    pair. Its `answers` are the miner's output and are deliberately not touched:
    an arm that quietly reads them measures nothing.

    `issues` are the linked issue reports (``number``, ``title``, ``body``) and
    `comments` their discussion together with the PR's. They are here because a
    numerical bug's REPORTED VALUES usually live in the issue rather than in the
    pull request — xarray#11478 says "the MCVE now returns the expected values"
    and only issue #11462 prints the -1.84e19 — and the miner's corpus contains
    them too. Withholding them would make this arm measure missing issue text as
    much as missing retrieval.
    """
    lines = [
        f"REPOSITORY: {record.get('repo')}   PR: {record.get('reference')}",
        f"BUGGY COMMIT (checked out): {record.get('buggy_commit')}",
        f"FIXED COMMIT (for reference only): {record.get('fixed_commit')}",
        "",
        "=== PULL REQUEST ===",
        f"TITLE: {title}",
        "BODY:",
        (body or "(empty)").strip()[:8000],
    ]
    for issue in issues or ():
        number = issue.get("number") if isinstance(issue, dict) else getattr(issue, "number", "")
        i_title = issue.get("title") if isinstance(issue, dict) else getattr(issue, "title", "")
        i_body = issue.get("body") if isinstance(issue, dict) else getattr(issue, "body", "")
        lines += ["", f"=== LINKED ISSUE #{number}: {i_title} ===",
                  (str(i_body or "(empty)")).strip()[:max_issue_body]]
    discussion = _discussion(comments or ())
    if discussion:
        lines += ["", "=== LINKED-ISSUE DISCUSSION ===", discussion]
    if diff and diff.strip():
        lines += ["", "=== PULL REQUEST DIFF (the fix AND its test; the test's "
                  "assertions carry the concrete input/expected-output) ===",
                  diff.strip()[:max_diff]]
    lines += ["", "=== README (" + (f"{len(readme)} chars" if readme else "none found") + ") ===",
              (readme or "(this repository has no README at this commit)").strip()[:max_readme]]
    # The project's CI and environment YAML: without the mined Q4 this is the
    # only statement of how the project builds and tests itself — the Python it
    # runs on, the apt and pip packages it installs, how it calls pytest. Added
    # LAST and under one budget, so a repository with dozens of workflows cannot
    # push the pull request out of the context window.
    if configs:
        budget = max_configs
        lines += ["", "=== CI / ENVIRONMENT YAML (how the project builds and tests "
                  "itself; use it for environment_setup and build_steps) ==="]
        for config in configs:
            text = str(config.get("text") or "")[:budget]
            if not text.strip():
                continue
            lines += [f"--- {config.get('path')} ---", text.strip()]
            budget -= len(text)
            if budget <= 0:
                lines.append("(remaining YAML omitted: context budget)")
                break
    lines += [
        "",
        "Produce the reproduction strategy as JSON per the schema, from the pull "
        "request, its linked issues and discussion, and the README above — and "
        "nothing else. Populate input_output_examples from the reported values in "
        "the issue or PR and the assertions in the test diff.",
    ]
    return "\n".join(lines)


def _ans(record: dict, qid: str) -> dict:
    return (record.get("answers", {}).get(qid, {}) or {}).get("answer", {}) or {}


def build_prompt(record: dict, test_patch: str = "") -> str:
    """Format a mined candidate record into the user prompt (the evidence block).

    `test_patch`, when provided, is the reproducing test's diff — the most reliable
    source of the concrete input/expected-output the strategy should record."""
    a = lambda q: _ans(record, q)  # noqa: E731
    lines = [
        f"REPOSITORY: {record.get('repo')}   PR: {record.get('reference')}",
        f"BUGGY COMMIT (checked out): {record.get('buggy_commit')}",
        f"FIXED COMMIT (for reference only): {record.get('fixed_commit')}",
        f"LINKED ISSUES: {record.get('linked_issues')}",
        f"MODIFIED FILES: {record.get('modified_files')}",
        f"TEST FILES (PR diff): {record.get('test_files')}",
        "",
        "=== MINED EVIDENCE (structured answers) ===",
        # the questions of the current miner. Records produced before they were
        # consolidated carry the older ids, so both are read: a strategy must be
        # buildable from a candidate mined last month.
    ]
    # An ABSENT question is left out rather than printed empty: an older record
    # missing a question would otherwise carry an empty line for it, telling the
    # model a question existed and
    # was withheld. A question the miner never answered reads the same way.
    for label, data in (
            ("Q2 triggering input & behaviours", a("behavior")),
            ("Q3 tests", a("tests") or a("reproducing_test")),
            ("Q4 build & reproduction", a("build_repro"))):
        if data:
            lines.append(f"{label}: {json.dumps(data)}")
    legacy = [("category", a("category")), ("evidence", a("evidence")),
              ("build & test", a("build_and_test")), ("commands", a("commands")),
              ("environment", a("environment")),
              ("execution requirements", a("execution_requirements")),
              ("reproducing test", a("reproducing_test")),
              ("fix files (WHERE the bug lives; do not apply the fix)", a("fix_files")),
              ("cross-doc conflicts", a("conflicts"))]
    lines += [f"{label}: {json.dumps(data)}" for label, data in legacy if data]
    if test_patch and test_patch.strip():
        # the reproducing test diff — the concrete input & expected output live here
        lines += [
            "",
            "=== REPRODUCING TEST DIFF (extract the concrete input/expected-output from these assertions) ===",
            test_patch.strip()[:6000],
        ]
    lines += [
        "",
        "Produce the reproduction strategy as JSON per the schema. Populate "
        "input_output_examples from the reported example(s) and the test assertions above.",
    ]
    return "\n".join(lines)


def strategy(record: dict, *, provider: str = "anthropic", model: str | None = None,
             host: str = providers.OLLAMA_HOST, max_tokens: int = 4096,
             test_patch: str = "", pr_context: dict | None = None) -> dict:
    """Generate the reproduction strategy for one mined candidate record.

    `test_patch` (the reproducing test diff) is fed to the model so it can record
    the concrete input/expected-output in `input_output_examples`.

    `pr_context` switches the run to the NO-MINER ablation arm: given
    ``{"title", "body", "diff", "readme"}``, the strategy is written from the
    pull request and the README alone and the mined answers are not read at all.
    The result carries ``evidence: "pr+readme"`` so the two arms can never be
    confused downstream — they produce the same file, for the same consumer, and
    only this field says which one wrote it.
    """
    model = model or providers.DEFAULT_CHAT_MODEL[provider]
    evidence_size = {}
    if pr_context is None:
        system, prompt = _SYSTEM, build_prompt(record, test_patch)
    else:
        system = _SYSTEM_PR_README
        prompt = build_prompt_pr_readme(record, **pr_context)
        # What the tokens were SPENT ON. A no-RAG prompt's size is the PR's
        # diff plus the README, both of which vary by an order of magnitude
        # across the corpus, so a token count on its own does not say whether an
        # instance was expensive because its diff was large or because its
        # README was a tutorial — and an instance whose README is missing
        # entirely is a different arm from the one whose README is 12k of badges.
        evidence_size = {
            "yaml_chars": sum(len(str(c.get("text") or ""))
                              for c in (pr_context.get("configs") or ())),
            "n_config_files": len(pr_context.get("configs") or ()),
            "prompt_chars": len(prompt),
            "pr_body_chars": len(str(pr_context.get("body") or "")),
            "diff_chars": len(str(pr_context.get("diff") or "")),
            "readme_chars": len(str(pr_context.get("readme") or "")),
            "n_linked_issues": len(pr_context.get("issues") or ()),
            "n_issue_comments": len(pr_context.get("comments") or ()),
        }
    res = providers.chat(provider, model, system, prompt,
                         schema=_SCHEMA, host=host, max_tokens=max_tokens)
    try:
        plan = providers.extract_json(res.text)
        error = ""
    except ValueError as e:
        plan, error = {}, str(e)
    return {
        "reference": record.get("reference"), "repo": record.get("repo"),
        "pr_number": record.get("pr_number"), "buggy_commit": record.get("buggy_commit"),
        "fixed_commit": record.get("fixed_commit"),
        # The bug category is CURATED, not mined: the classification question is
        # gone, so this is empty unless a caller fills it from the annotated CSV
        # (`numbugminer mine --categories`, or the index that
        # make_index_with_bugtype.py writes, which `specs_from_index` prefers).
        # An old record that still carries a mined category keeps it.
        "bug_type": ("" if pr_context is not None else
                     _ans(record, "category").get("category")),
        "provider": provider, "model": model,
        # the label says WHICH no-miner arm wrote this: the original PR+README
        # one, or the ablation that also reads the project's CI/environment YAML
        "evidence": ("mined" if pr_context is None
                     else "pr+readme+yaml" if pr_context.get("configs")
                     else "pr+readme"),
        "evidence_size": evidence_size or None,
        "strategy": plan, "error": error,
        # Per-strategy token consumption: input / output / cached tokens and the
        # dollar cost of THIS call. Kept per record, not only in the run's
        # totals, because the arms are compared instance by instance.
        "usage": res.usage.to_dict() if res.usage else None,
    }

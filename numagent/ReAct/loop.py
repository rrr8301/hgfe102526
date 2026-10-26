from __future__ import annotations

import json

from ..agent.llm.base import LLMClient, build_client
from ..repro_package import PackageOptions
from ..repro_session import AgentReproResult, run_session
from ..repro_specs import ReproSpec
from ..planning import (ensure_described_examples, ensure_examples_observed,
                        ensure_reproducing_test_ran,
                        ensure_test_on_both_revisions)
from ..repro_tools import RunContext, available_tools, registry
from . import prompts

_RESULT_LIMIT = 6000  # chars of a tool result fed back to the model

NAME = "react"


def _drive(ctx: RunContext, client: LLMClient, max_iterations: int) -> str:
    """The ReAct loop itself. Returns an error message, or "" if it finished."""
    # the tools may GENERATE code (numagent.probe_codegen), which needs the
    # run's own client rather than a second one built from the environment
    ctx.llm = client
    tools = available_tools(ctx)          # packaging is offered only when allowed
    messages = [
        {"role": "system",
         "content": prompts.system("package_benchmark_image" in tools)},
        {"role": "user", "content": prompts.build_task(ctx.spec)},
    ]
    schemas = registry.schemas(tools)
    while ctx.iterations < max_iterations:
        ctx.iterations += 1
        turn = client.complete(messages, schemas)
        if turn.text:
            ctx.emit("thought", turn.text)
            ctx.history.append({"thought": turn.text[:800]})
        messages.append({"role": "assistant", "text": turn.text,
                         "tool_calls": turn.tool_calls})

        # a turn with no Action: prompt for one rather than burning the budget
        if not turn.wants_tools:
            if ctx.iterations >= max_iterations:
                break
            messages.append({"role": "user",
                             "content": "Continue the procedure, or call "
                                        "finalize_reproduction if done."})
            continue

        for tc in turn.tool_calls:
            # the agent is about to judge the run: make sure the report's own
            # input/output examples were actually evaluated on both revisions
            if tc.name in ("compare_execution_results", "finalize_reproduction"):
                ensure_reproducing_test_ran(ctx)
                ensure_test_on_both_revisions(ctx)
                if ctx.probes:
                    ensure_examples_observed(ctx, client)
                    ensure_described_examples(ctx, client)
            result = registry.call(tc.name, ctx, tc.arguments or {})
            messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                             "content": json.dumps(result, default=str)[:_RESULT_LIMIT]})
            if tc.name == "finalize_reproduction":
                return ""

        # nudge toward a verdict as the budget runs out
        if ctx.iterations >= max_iterations - 2 and ctx.report is None:
            messages.append({"role": "user", "content": (
                "You are almost out of steps. Ensure the reproducing test has run on "
                "both /workspace/buggy and /workspace/fixed, then call "
                "compare_execution_results and finalize_reproduction now.")})

    return f"agent did not finalize within {max_iterations} iterations"


def reproduce(spec: ReproSpec, *, client: LLMClient | None = None,
              provider: str = "anthropic", model: str = "", work_dir,
              token: str | None = None, max_iterations: int = 30,
              cmd_timeout_s: float = 1800.0, keep_container: bool = False,
              package: PackageOptions | None = None, stream_output: bool = False,
              probes: bool = True, on_event=None) -> AgentReproResult:
    """Reproduce one bug with ReAct reasoning over the shared toolset."""
    client = client or build_client(provider, model)
    return run_session(
        spec, reasoning=NAME, work_dir=work_dir, token=token,
        cmd_timeout_s=cmd_timeout_s, keep_container=keep_container,
        package=package, stream_output=stream_output, probes=probes,
        on_event=on_event,
        driver=lambda ctx: _drive(ctx, client, max_iterations))

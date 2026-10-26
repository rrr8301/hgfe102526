from __future__ import annotations

import json
from typing import Any, Dict, List

from .base import AssistantTurn, LLMClient, ToolCall


class OpenAIClient(LLMClient):
    name = "openai"

    def __init__(self, model: str = "gpt-4.1-mini", api_key: str = None,
                 base_url: str = None, name: str = ""):
        """`base_url` points this client at an OpenAI-COMPATIBLE endpoint.

        DeepSeek serves the same Chat Completions shape with tools, so it needs
        no client of its own — only its host and key. `name` is what the run
        reports as the provider, so a DeepSeek run is not labelled "openai".
        """
        import openai  # lazy

        super().__init__()
        self.model = model
        options = {k: v for k, v in (("api_key", api_key), ("base_url", base_url)) if v}
        self._client = openai.OpenAI(**options)
        if name:
            self.name = name

    # --- neutral -> OpenAI ----------------------------------------------- #
    @staticmethod
    def _to_native(messages: List[Dict[str, Any]]):
        native: List[Dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role in ("system", "user"):
                native.append({"role": role, "content": m["content"]})
            elif role == "assistant":
                msg: Dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m["tool_calls"]
                    ]
                native.append(msg)
            elif role == "tool":
                native.append({
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": m["content"],
                })
        return native

    @staticmethod
    def _tools(tools: List[Dict[str, Any]]):
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]

    # --- request --------------------------------------------------------- #
    #: Models that reject `max_tokens` and want `max_completion_tokens` instead —
    #: the reasoning-era families, where the cap has to cover reasoning as well
    #: as the reply. Matched by prefix so dated ids resolve too.
    _COMPLETION_TOKENS_MODELS = ("gpt-5", "o1", "o3", "o4")

    #: A reasoning endpoint spends output tokens THINKING before it answers, and
    #: DeepSeek counts those in `completion_tokens`. A budget sized for the reply
    #: alone is consumed by the reasoning and the answer comes back empty — read
    #: downstream as a refusal or a broken tool call rather than a budget.
    #:
    #: 2x, not the miner's 4x: at 4x (a 32k cap) the first 17 pandas instances
    #: spent ~86k output tokens EACH — 1.46M for 17 — because the model fills the
    #: room it is given, and output is the expensive half ($1.32/1M). The miner
    #: asks for one JSON answer per call; an agent loop asks for dozens, so the
    #: same multiplier costs an order of magnitude more here.
    _REASONING_BUDGET_MULT = 2

    def complete(self, messages, tools, max_tokens: int = 8192) -> AssistantTurn:
        cap = ("max_completion_tokens"
               if self.model.startswith(self._COMPLETION_TOKENS_MODELS) else "max_tokens")
        if self.name == "deepseek":
            max_tokens *= self._REASONING_BUDGET_MULT
        resp = self._client.chat.completions.create(
            model=self.model,
            tools=self._tools(tools),
            messages=self._to_native(messages),
            **{cap: max_tokens},
        )
        self.record_usage(getattr(resp, "usage", None))
        choice = resp.choices[0]
        msg = choice.message
        calls: List[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return AssistantTurn(
            text=msg.content or "",
            tool_calls=calls,
            stop_reason=choice.finish_reason or "",
            raw=resp,
        )

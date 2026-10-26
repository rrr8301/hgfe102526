from __future__ import annotations

from typing import Any, Dict, List

from .base import AssistantTurn, LLMClient, ToolCall


class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, model: str = "claude-haiku-4-5-20251001", api_key: str = None):
        import anthropic  # lazy: only needed when this provider is used

        super().__init__()
        self.model = model
        # Zero-arg client resolves ANTHROPIC_API_KEY or an `ant auth login` profile.
        self._client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    # --- neutral -> Anthropic -------------------------------------------- #
    @staticmethod
    def _to_native(messages: List[Dict[str, Any]]):
        system_parts: List[str] = []
        native: List[Dict[str, Any]] = []

        for m in messages:
            role = m["role"]
            if role == "system":
                system_parts.append(m["content"])
            elif role == "user":
                native.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                content: List[Dict[str, Any]] = []
                if m.get("text"):
                    content.append({"type": "text", "text": m["text"]})
                for tc in m.get("tool_calls", []):
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
                native.append({"role": "assistant", "content": content})
            elif role == "tool":
                # tool_result blocks must live in a user message. Merge consecutive
                # tool messages into the trailing user turn.
                block = {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"],
                }
                if native and native[-1]["role"] == "user" and isinstance(native[-1]["content"], list):
                    native[-1]["content"].append(block)
                else:
                    native.append({"role": "user", "content": [block]})

        return "\n\n".join(system_parts), native

    @staticmethod
    def _tools(tools: List[Dict[str, Any]]):
        return [
            {"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
            for t in tools
        ]

    # --- request --------------------------------------------------------- #
    def complete(self, messages, tools, max_tokens: int = 8192) -> AssistantTurn:
        system, native = self._to_native(messages)
        # Haiku 4.5 does not support the `effort` / adaptive-thinking params, so
        # we deliberately keep the request minimal.
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system or None,
            tools=self._tools(tools),
            messages=native,
        )
        self.record_usage(getattr(resp, "usage", None))
        text_parts: List[str] = []
        calls: List[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return AssistantTurn(
            text="".join(text_parts),
            tool_calls=calls,
            stop_reason=resp.stop_reason or "",
            raw=resp,
        )

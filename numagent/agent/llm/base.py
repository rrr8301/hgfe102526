from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


# USD per 1M tokens (input, output) for the non-Claude models we use; Claude
# rates come from numbugminer.costs so there is one price table for them.
# USD per 1M tokens (input, output), from OpenAI's published API pricing
# (developers.openai.com/api/docs/pricing, checked 2026-09-07). Longest matching
# prefix wins, so a dated id like gpt-5.4-mini-2026-03-17 prices as gpt-5.4-mini.
_OPENAI_PRICING: Dict[str, tuple] = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}

#: OpenAI bills a cached input token at a tenth of the input rate. Charging it in
#: full — as this did — overstates a long agent run, where most of the prompt is
#: the same transcript resent turn after turn and therefore nearly all cached.
_OPENAI_CACHE_READ_MULT = 0.1


#: Anthropic models whose rates `numbugminer.costs` does not carry. That table is
#: the single price list for Claude and is not ours to edit, so a model released
#: after it was written prices at $0.00 — which reads as "free" rather than
#: "unpriced". These are consulted first; everything else still delegates.
#:
#: Claude Haiku 5.5 is tiered: $0.10/$0.50 per MTok for prompts up to 100K
#: tokens, $0.50/$2.50 above it. The figures below are the ≤100K tier, which is
#: the one this project bills at — a reproduction prompt is a few thousand
#: tokens — and `estimate_cost` receives per-run totals, not per-call prompt
#: sizes, so the tier cannot be reconstructed here. A run whose single prompts
#: exceed 100K is therefore UNDER-reported 5x; nothing in this benchmark comes
#: close, but a long-context agent would need the tier applied per call.
_ANTHROPIC_EXTRA_PRICING: Dict[str, tuple] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

#: Anthropic bills a cache read at a quarter of the input rate for the 5.5
#: family ($0.20/MTok on Sonnet and Opus 5.5); the ratio, not the absolute, is
#: what applies to Haiku.
_ANTHROPIC_CACHE_READ_MULT = 0.1
_ANTHROPIC_CACHE_WRITE_MULT = 1.25


def estimate_cost(model: str, input_tokens: int, output_tokens: int,
                  cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    """USD for a token count, or 0.0 when the model isn't in a price table."""
    extra = [k for k in _ANTHROPIC_EXTRA_PRICING if model.startswith(k)]
    if extra:
        inp, out = _ANTHROPIC_EXTRA_PRICING[max(extra, key=len)]
        return round(input_tokens * inp / 1e6
                     + cache_read_tokens * inp * _ANTHROPIC_CACHE_READ_MULT / 1e6
                     + cache_write_tokens * inp * _ANTHROPIC_CACHE_WRITE_MULT / 1e6
                     + output_tokens * out / 1e6, 6)
    # numbugminer.costs holds the Claude rates AND DeepSeek's, whose time-of-day
    # and cache-read multipliers it already applies — a DeepSeek run priced here
    # instead reported $0.00, which reads as "free" rather than "unpriced".
    if model.startswith(("claude", "deepseek")):
        from numbugminer.costs import Usage
        return Usage(model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                     cache_read_tokens=cache_read_tokens,
                     cache_write_tokens=cache_write_tokens).cost_usd
    matches = [k for k in _OPENAI_PRICING if model.startswith(k)]
    if not matches:
        return 0.0
    inp, out = _OPENAI_PRICING[max(matches, key=len)]
    return round((input_tokens + cache_write_tokens) * inp / 1e6
                 + cache_read_tokens * inp * _OPENAI_CACHE_READ_MULT / 1e6
                 + output_tokens * out / 1e6, 6)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class AssistantTurn:
    """One assistant response in neutral form."""
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = ""
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient(abc.ABC):
    """A chat-completion backend that supports tool use.

    Adapters call `record_usage` after every response so a caller can report
    what a run consumed (`usage`) and what it cost (`cost_usd`).
    """

    name: str = "llm"
    model: str = ""

    def __init__(self):
        self.reset_usage()

    def reset_usage(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0

    def record_usage(self, u) -> None:
        """Accumulate one response's token counts (missing fields count as 0)."""
        g = lambda *names: next(  # noqa: E731
            (int(getattr(u, n, None) or 0) for n in names if getattr(u, n, None)), 0)
        self.calls += 1
        self.input_tokens += g("input_tokens", "prompt_tokens")
        self.output_tokens += g("output_tokens", "completion_tokens")
        self.cache_read_tokens += g("cache_read_input_tokens")
        self.cache_write_tokens += g("cache_creation_input_tokens")

    @property
    def usage(self) -> Dict[str, Any]:
        return {"model": self.model, "calls": self.calls,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "cost_usd": self.cost_usd}

    @property
    def cost_usd(self) -> float:
        return estimate_cost(self.model, self.input_tokens, self.output_tokens,
                             self.cache_read_tokens, self.cache_write_tokens)

    @abc.abstractmethod
    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 8192,
    ) -> AssistantTurn:
        """Send the neutral transcript + tool schemas, return one assistant turn."""
        raise NotImplementedError


def complete_json_turn(client: LLMClient, system: str, user: str, schema: Dict[str, Any],
                       name: str, description: str = "",
                       max_tokens: int = 8192) -> Tuple[Dict[str, Any], AssistantTurn]:
    """:func:`complete_json`, plus the turn it came from.

    The turn carries ``stop_reason``, which is the difference between "the model
    had nothing to say" and "the answer was cut off mid-object at the token
    limit" — identical-looking empty dicts that call for opposite responses.
    """
    turn = client.complete(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        [{"name": name, "description": description or f"Submit the {name} result.",
          "parameters": schema}],
        max_tokens=max_tokens,
    )
    for tc in turn.tool_calls:
        if tc.name == name and isinstance(tc.arguments, dict):
            return tc.arguments, turn
    return _json_from_text(turn.text), turn


def complete_json(client: LLMClient, system: str, user: str, schema: Dict[str, Any],
                  name: str, description: str = "", max_tokens: int = 8192) -> Dict[str, Any]:
    """One LLM call whose answer is a JSON object matching `schema`.

    Implemented by offering exactly one tool and reading back its arguments —
    both provider adapters already parse those into a dict, so this needs no
    per-provider structured-output support. A model that answers in prose
    instead is salvaged by pulling the JSON object out of its text; when even
    that fails the caller gets {} and decides what to do.
    """
    return complete_json_turn(client, system, user, schema, name, description,
                              max_tokens)[0]


def _json_from_text(text: str) -> Dict[str, Any]:
    """Best-effort: the whole text as JSON, else the widest {...} span in it."""
    import json
    import re

    for candidate in (text or "", *(m.group(0) for m in
                                    re.finditer(r"\{.*\}", text or "", re.DOTALL))):
        try:
            parsed = json.loads(candidate.strip().strip("`"))
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def build_client(provider: str, model: str = "", **kwargs) -> LLMClient:
    """Factory: 'anthropic' or 'openai'. Model defaults are the ones chosen for
    this project (Haiku 4.5 / gpt-4.1-mini)."""
    provider = provider.lower()
    if provider == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(model=model or "claude-haiku-4-5-20251001", **kwargs)
    if provider == "openai":
        from .openai_client import OpenAIClient
        return OpenAIClient(model=model or "gpt-4.1-mini", **kwargs)
    if provider == "deepseek":
        # DeepSeek speaks the OpenAI Chat Completions API, tools included, so it
        # reuses that client against its own host and key. Same table the miner
        # uses, so `mine` and `repro` cannot drift apart on the endpoint.
        import os

        from numbugminer.providers import DEFAULT_CHAT_MODEL, OPENAI_COMPATIBLE

        from .openai_client import OpenAIClient
        config = OPENAI_COMPATIBLE["deepseek"]
        kwargs.setdefault("api_key", os.environ.get(config["api_key_env"]))
        return OpenAIClient(model=model or DEFAULT_CHAT_MODEL["deepseek"],
                            base_url=config["base_url"], name="deepseek", **kwargs)
    raise ValueError(f"unknown provider {provider!r} "
                     f"(expected 'anthropic', 'openai' or 'deepseek')")

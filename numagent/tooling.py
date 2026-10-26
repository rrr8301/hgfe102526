from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]   # JSON Schema for the tool arguments
    func: Callable

    def schema(self) -> Dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


class ToolRegistry:
    """Holds decorated tools and exposes their schemas + a dispatcher."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def tool(self, *, parameters: Dict[str, Any], name: str | None = None,
             description: str | None = None) -> Callable:
        """Decorator: register `func` as a tool.

        `parameters` is the JSON Schema for the tool's arguments; the description
        defaults to the function's docstring.
        """
        def deco(func: Callable) -> Callable:
            tname = name or func.__name__
            desc = (description or func.__doc__ or "").strip()
            self._tools[tname] = Tool(tname, desc, parameters, func)
            return func
        return deco

    def names(self) -> List[str]:
        return list(self._tools)

    def schemas(self, names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        names = names or self.names()
        return [self._tools[n].schema() for n in names]

    def call(self, name: str, ctx: Any, args: Dict[str, Any] | None) -> Any:
        """Dispatch a tool call, tolerating what a model gets wrong.

        An argument the schema does not declare is dropped (and reported back)
        and a bad signature becomes an error result — a hallucinated keyword
        must cost one observation, not the whole attempt.
        """
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"unknown tool {name!r}", "known_tools": self.names()}
        args = dict(args or {})
        props = (tool.parameters or {}).get("properties")
        ignored = sorted(k for k in args if k not in props) if isinstance(props, dict) else []
        for k in ignored:
            args.pop(k)
        try:
            result = tool.func(ctx, **args)
        except TypeError as e:               # missing/extra positional-only args
            return {"error": f"bad arguments for {name!r}: {e}"}
        if ignored and isinstance(result, dict):
            result["ignored_arguments"] = ignored
        return result

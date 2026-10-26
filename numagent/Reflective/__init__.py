"""Self-reflective planning over the shared reproduction toolset.

Plan the whole procedure, execute it, and reflect only when an observation
contradicts what the plan predicted. Reflection is an orchestrator-controlled
LLM call, NOT a tool — the toolset is exactly the shared four.
"""

from .loop import NAME, reproduce

__all__ = ["NAME", "reproduce"]

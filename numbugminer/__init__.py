"""NumBugMiner — a RAG-based numerical-bug miner for CI logs.

Given a failed GitHub Actions run (id or URL), download its logs, extract the
regions that carry numerical signals (NaN / Inf / overflow / underflow /
divide-by-zero), retrieve similar labeled examples from a local knowledge base,
and ask Claude to decide whether the failure is a genuine numerical bug.
"""

__version__ = "0.1.0"

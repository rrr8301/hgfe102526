"""NumericSpecificMiner — a numerical-aware, retrieval-augmented context miner.

It does NOT decide whether a pull request fixes a numerical bug. That judgement
belongs to the Human–LLM annotation procedure that produced the benchmark, and
this package operates on already-confirmed instances: the annotated CATEGORY is
an input here, not an output.

The pipeline is one PR at a time:

    PR link -> crawl -> clone at the buggy commit -> task view -> RAG (3 Qs)
            -> compact structured context -> strategy

Everything numerical-aware is written here. The I/O primitives — the GitHub
fetch, the clone, the Chroma index, the provider clients — are imported from
`numbugminer`, which this package never modifies.

Modules
    questions   the three questions, their schemas and category query expansion
    views       task-specific evidence views (HBEC / BRTG), filtered BEFORE RAG
    era         era-aware dependency reconstruction
    context     the compact structured context, with provenance
    miner       the pipeline
    expand      adaptive evidence expansion driven by a reproduction failure
    strategy    context -> the *.strategies.json the reproducer consumes
    cli         the command line
"""
from .context import NumericalContext
from .questions import CATEGORIES, QUESTIONS
from .views import VIEWS

__all__ = ["NumericalContext", "QUESTIONS", "CATEGORIES", "VIEWS"]
__version__ = "0.1.0"

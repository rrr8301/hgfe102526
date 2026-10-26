"""Opening a mined index without destroying it.

`numbugminer.chroma_store.ChromaStore.__init__` deletes the collection it is
handed — "rebuild the collection each run so a re-mine of the same PR is clean".
That is right for mining and fatal for reading: every component that opened a
store in order to QUERY an existing index (the escalation ladder, the targeted
expansion, the reproducer's mid-run evidence call) wiped the collection first and
then retrieved from nothing, reporting `n_passages: 0` with no error anywhere.

This reader opens the collection instead. numbugminer is not modified: the class
is subclassed and only the constructor is replaced, so `query`, `query_balanced`
and `add` are the originals.
"""
from __future__ import annotations

from numbugminer import providers
from numbugminer.chroma_store import ChromaStore


class ReadOnlyStore(ChromaStore):
    """A ChromaStore that opens its collection rather than rebuilding it."""

    def __init__(self, persist_dir, collection: str, *, provider: str = "ollama",
                 embed_model: str | None = None, host: str = "",
                 embedder=None):
        import chromadb

        self.embedder = embedder or providers.make_embedder(
            provider, embed_model, host or providers.OLLAMA_HOST)
        self.client = chromadb.PersistentClient(path=str(persist_dir))
        self.collection = self.client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"})

    @property
    def is_empty(self) -> bool:
        """True when there is nothing indexed — the caller should re-index rather
        than conclude the evidence is silent."""
        try:
            return self.collection.count() == 0
        except Exception:                            # noqa: BLE001
            return False

from __future__ import annotations

from dataclasses import dataclass, field

from .providers import Embedder, make_embedder

# Chroma metadata values must be str/int/float/bool — never None or lists.
_META_KEYS = ("source_type", "repo", "pr_number", "commit_sha", "file_path",
              "heading", "line_start", "line_end")
# Defensive cap so a stray oversized chunk can't blow a small embedder's context
# window (e.g. nomic-embed-text ~2048 tokens). Chunks are already ~1.2k chars.
_MAX_EMBED_CHARS = 4000


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict = field(default_factory=dict)


def _clean_meta(m: dict) -> dict:
    out = {}
    for k in _META_KEYS:
        v = m.get(k)
        if v is None:
            v = 0 if k in ("pr_number", "line_start", "line_end") else ""
        out[k] = v
    return out


class ChromaStore:
    """A persistent per-PR ChromaDB collection with a shared Ollama embedder."""

    def __init__(self, persist_dir, collection: str, *, provider: str = "ollama",
                 embed_model: str | None = None, host: str = "http://localhost:11434",
                 embedder: Embedder | None = None):
        import chromadb

        self.embedder = embedder or make_embedder(provider, embed_model, host)
        self.client = chromadb.PersistentClient(path=str(persist_dir))
        # Rebuild the collection each run so a re-mine of the same PR is clean.
        try:
            self.client.delete_collection(collection)
        except Exception:  # noqa: BLE001 — collection may not exist yet
            pass
        self.collection = self.client.create_collection(
            collection, metadata={"hnsw:space": "cosine"})

    def add(self, chunks: list[Chunk], batch: int = 128) -> int:
        """Embed and index `chunks` (TF-IDF embedders are fit on the full set first)."""
        if not chunks:
            return 0
        texts = [c.text for c in chunks]
        if hasattr(self.embedder, "fit"):
            self.embedder.fit(texts)  # no-op for API embedders; fits TF-IDF fallback
        for i in range(0, len(chunks), batch):
            part = chunks[i:i + batch]
            embs = self.embedder.encode([c.text[:_MAX_EMBED_CHARS] for c in part])
            self.collection.add(
                ids=[c.id for c in part],
                documents=[c.text for c in part],
                embeddings=[e.tolist() for e in embs],
                metadatas=[_clean_meta(c.metadata) for c in part],
            )
        return len(chunks)

    def query(self, text: str, k: int = 6,
              source_types: tuple[str, ...] | None = None) -> list[dict]:
        """Top-k chunks by cosine similarity, optionally restricted to source types
        (falls back to unrestricted when the filter yields too few)."""
        n = self.collection.count()
        if n == 0:
            return []
        q = self.embedder.encode([text])[0].tolist()

        def run(where):
            r = self.collection.query(query_embeddings=[q], n_results=min(k, n), where=where)
            docs = (r.get("documents") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            dists = (r.get("distances") or [[]])[0]
            return [{"text": d, "metadata": m, "score": round(1.0 - float(dist), 3)}
                    for d, m, dist in zip(docs, metas, dists)]

        if source_types:
            where = ({"source_type": source_types[0]} if len(source_types) == 1
                     else {"source_type": {"$in": list(source_types)}})
            hits = run(where)
            if len(hits) >= k:
                return hits
        return run(None)

    def query_balanced(self, text: str, k: int = 6,
                       source_types: tuple[str, ...] | None = None) -> list[dict]:
        """Top-k chunks with a quota per source type.

        Plain top-k lets the most verbose source win outright: a repository's CI
        workflows, or a fetched documentation page, out-rank the three-line diff
        hunk that actually answers the question simply by having more text that
        looks on-topic. A question naming several sources wants evidence from
        each, so each gets a share and the leftover slots go by global rank.
        """
        if not source_types:
            return self.query(text, k=k)
        n = self.collection.count()
        if n == 0:
            return []
        q = self.embedder.encode([text])[0].tolist()   # embed once, query per source

        def run(where, limit):
            r = self.collection.query(query_embeddings=[q],
                                      n_results=min(max(limit, 1), n), where=where)
            docs = (r.get("documents") or [[]])[0]
            metas = (r.get("metadatas") or [[]])[0]
            dists = (r.get("distances") or [[]])[0]
            return [{"text": d, "metadata": m, "score": round(1.0 - float(dist), 3)}
                    for d, m, dist in zip(docs, metas, dists)]

        quota = max(1, k // max(len(source_types), 1))
        picked, seen = [], set()
        for i, st in enumerate(source_types):
            # the first-named source is the question's primary evidence
            take = quota + (1 if i == 0 else 0)
            for hit in run({"source_type": st}, take):
                key = (hit["metadata"].get("source_type"), hit["text"][:120])
                if key not in seen:
                    seen.add(key)
                    picked.append(hit)
        picked.sort(key=lambda h: -h["score"])
        if len(picked) < k:
            # Top up by global rank — but only into the slots the quota left
            # empty. Sorting the union instead lets the verbose source reclaim
            # every slot, which is the failure the quota exists to prevent: a
            # question whose issues are missing would come back answered entirely
            # from CI workflows.
            where = ({"source_type": source_types[0]} if len(source_types) == 1
                     else {"source_type": {"$in": list(source_types)}})
            for hit in run(where, k):
                if len(picked) >= k:
                    break
                key = (hit["metadata"].get("source_type"), hit["text"][:120])
                if key not in seen:
                    seen.add(key)
                    picked.append(hit)
        return picked[:k]


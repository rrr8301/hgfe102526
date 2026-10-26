"""Local (offline) dense retrieval over the seed corpus.

Two interchangeable embedding backends, selected by `embed_backend`:

  * **sentence-transformers** — dense semantic embeddings (best quality, needs
    torch). Corpus vectors are cached to disk keyed by (model, corpus content).
  * **tfidf** — a self-contained NumPy TF-IDF vectoriser (no torch, no network,
    fully reproducible). Used automatically when sentence-transformers isn't
    importable, so the pipeline always runs out of the box.

Both expose L2-normalised vectors, so a dot product is cosine similarity.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .knowledge_base import Example

_TOKEN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*|\d+")


@dataclass(frozen=True)
class Retrieved:
    example: Example
    score: float


def resolve_backend(backend: str) -> str:
    """Map the configured backend ('auto'|'tfidf'|'sentence-transformers') to a
    concrete one, falling back to tfidf when sentence-transformers is absent."""
    if backend in ("tfidf", "sentence-transformers"):
        return backend
    # auto
    try:
        import sentence_transformers  # noqa: F401

        return "sentence-transformers"
    except Exception:
        return "tfidf"


# --- sentence-transformers backend ------------------------------------------
@lru_cache(maxsize=2)
def _load_st_model(name: str):
    from sentence_transformers import SentenceTransformer  # lazy: heavy import

    return SentenceTransformer(name)


class _STEmbedder:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def fit(self, corpus_texts: list[str]) -> None:  # no-op; model is pretrained
        pass

    def encode(self, texts: list[str]) -> np.ndarray:
        model = _load_st_model(self.model_name)
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


# --- TF-IDF backend ----------------------------------------------------------
def _tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class _TfidfEmbedder:
    """A minimal TF-IDF vectoriser over a fixed corpus vocabulary."""

    def __init__(self) -> None:
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray | None = None

    def fit(self, corpus_texts: list[str]) -> None:
        df: dict[str, int] = {}
        for text in corpus_texts:
            for tok in set(_tokenize(text)):
                df[tok] = df.get(tok, 0) + 1
        self.vocab = {tok: i for i, tok in enumerate(sorted(df))}
        n = max(1, len(corpus_texts))
        idf = np.ones(len(self.vocab), dtype=np.float32)
        for tok, i in self.vocab.items():
            idf[i] = math.log((1 + n) / (1 + df[tok])) + 1.0
        self.idf = idf

    def encode(self, texts: list[str]) -> np.ndarray:
        if self.idf is None:
            raise RuntimeError("TfidfEmbedder.encode called before fit().")
        out = np.zeros((len(texts), len(self.vocab)), dtype=np.float32)
        for r, text in enumerate(texts):
            for tok in _tokenize(text):
                j = self.vocab.get(tok)
                if j is not None:
                    out[r, j] += 1.0
            out[r] *= self.idf
            norm = np.linalg.norm(out[r])
            if norm > 0:
                out[r] /= norm
        return out


def _corpus_fingerprint(examples: list[Example], key: str) -> str:
    h = hashlib.sha256(key.encode())
    for ex in examples:
        h.update(ex.id.encode())
        h.update(ex.embed_text().encode())
    return h.hexdigest()[:16]


class Index:
    """A tiny in-memory cosine-similarity index over the corpus."""

    def __init__(self, examples: list[Example], embedder, matrix: np.ndarray):
        self.examples = examples
        self.embedder = embedder
        self.matrix = matrix  # (N, D), rows already L2-normalized

    @classmethod
    def build(
        cls,
        examples: list[Example],
        embed_model: str,
        embed_backend: str = "auto",
        cache_dir: Path | None = None,
    ) -> "Index":
        backend = resolve_backend(embed_backend)
        corpus_texts = [ex.embed_text() for ex in examples]

        if backend == "sentence-transformers":
            embedder = _STEmbedder(embed_model)
            embedder.fit(corpus_texts)
            cache_path = None
            if cache_dir is not None:
                cache_dir.mkdir(parents=True, exist_ok=True)
                fp = _corpus_fingerprint(examples, embed_model)
                cache_path = cache_dir / f"corpus_{fp}.npy"
                if cache_path.exists():
                    return cls(examples, embedder, np.load(cache_path))
            matrix = embedder.encode(corpus_texts)
            if cache_path is not None:
                np.save(cache_path, matrix)
            return cls(examples, embedder, matrix)

        # tfidf: fitting is cheap and query-independent; skip disk cache.
        embedder = _TfidfEmbedder()
        embedder.fit(corpus_texts)
        matrix = embedder.encode(corpus_texts)
        return cls(examples, embedder, matrix)

    def search(self, query: str, top_k: int) -> list[Retrieved]:
        q = self.embedder.encode([query])[0]  # normalized -> dot == cosine
        scores = self.matrix @ q
        order = np.argsort(-scores)[:top_k]
        return [Retrieved(self.examples[i], float(scores[i])) for i in order]

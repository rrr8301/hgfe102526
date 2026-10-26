from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = PROJECT_ROOT / ".cache"


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Populate os.environ from a .env file without adding a dependency.

    Only sets keys not already in the real environment, so an explicit
    `export FOO=...` always wins over the file. Runs at import time so the
    Config field defaults below see the loaded values.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


# Per-provider meta-prompted seed sets, plus the neutral hand-written anchor
# base each provider corpus is built on top of.
SEED_CORPUS_PATHS: dict[str, Path] = {
    "anthropic": DATA_DIR / "seed_corpus.claude.jsonl",
    "openai": DATA_DIR / "seed_corpus.openai.jsonl",
}
HAND_CORPUS_PATH = DATA_DIR / "seed_corpus.hand.jsonl"


def seed_corpus_for(provider: str) -> Path:
    return SEED_CORPUS_PATHS[provider]


@dataclass(frozen=True)
class Config:
    # Anthropic model used for the final classification / Claude generation.
    model: str = os.environ.get("NBM_MODEL", "claude-haiku-4-5-20251001")
    # OpenAI model used when --provider openai is selected for generation.
    openai_model: str = os.environ.get("NBM_OPENAI_MODEL", "gpt-4.1-mini")
    # sentence-transformers model used for local retrieval embeddings.
    embed_model: str = os.environ.get(
        "NBM_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    # Retrieval backend: "auto" (sentence-transformers if importable, else
    # tfidf), "sentence-transformers", or "tfidf".
    embed_backend: str = os.environ.get("NBM_EMBED_BACKEND", "auto")
    # How many labeled examples to retrieve as RAG context.
    top_k: int = int(os.environ.get("NBM_TOP_K", "5"))
    # GitHub token for the API (PR data, issues, logs). Higher rate limits +
    # access to logs; falls back to unauthenticated for public repos.
    github_token: str | None = os.environ.get("GITHUB_TOKEN") or None
    # Cap on how many characters of extracted evidence we hand to Claude.
    max_excerpt_chars: int = int(os.environ.get("NBM_MAX_EXCERPT_CHARS", "16000"))

    # Active corpus used by `analyze` retrieval. Defaults to the Claude set;
    # override with NBM_SEED_CORPUS or `analyze --corpus PATH`.
    seed_corpus_path: Path = Path(
        os.environ.get("NBM_SEED_CORPUS") or SEED_CORPUS_PATHS["anthropic"]
    )


def load_config() -> Config:
    return Config()

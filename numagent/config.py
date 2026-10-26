from __future__ import annotations

import os
from typing import Optional


def load_env(path: Optional[str] = None) -> Optional[str]:
    """Load a .env file. Returns the path loaded, or None if none was found."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:
        return None  # dotenv is optional; real-env vars still work

    dotenv_path = path or find_dotenv(usecwd=True)
    if not dotenv_path:
        return None
    load_dotenv(dotenv_path, override=False)
    return dotenv_path


def require_key(provider: str) -> str:
    """Return the API key for a provider, or raise a clear error naming the var."""
    var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    key = os.environ.get(var)
    if not key:
        raise RuntimeError(
            f"missing {var}. Add it to a .env file in the project root "
            f"(see .env.example) or export it in your shell."
        )
    return key

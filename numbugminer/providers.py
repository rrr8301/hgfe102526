from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

import numpy as np

from . import config as _config  # noqa: F401 — importing loads .env (ANTHROPIC/OPENAI keys)
from .costs import Usage

PROVIDERS = ("anthropic", "openai", "deepseek", "ollama")

#: Endpoints that speak the OpenAI chat-completions API. Adding a vendor is data,
#: not code — what differs between them is the base URL, the key, and how much of
#: OpenAI's structured-output support they actually implement.
#:
#: `json_schema` says the endpoint honours ``response_format={"type":
#: "json_schema", ...}``. DeepSeek implements JSON *mode* but not schema
#: enforcement, so its schema is carried in the prompt and the answer is parsed
#: with :func:`extract_json` — the same path Ollama models take.
OPENAI_COMPATIBLE = {
    "openai": {"base_url": None, "api_key_env": "OPENAI_API_KEY", "json_schema": True},
    # `reasoning`: the endpoint spends output tokens thinking before it answers
    # (DeepSeek returns them as `reasoning_content`, counted in
    # `completion_tokens`). A budget sized for the answer alone is consumed by
    # the reasoning and the content comes back EMPTY — which reads downstream as
    # "could not parse JSON" for what is really a budget problem.
    "deepseek": {"base_url": "https://api.deepseek.com/v1",
                 "api_key_env": "DEEPSEEK_API_KEY", "json_schema": False,
                 "reasoning": True, "budget_multiplier": 4,
                 "time_of_day_pricing": True},
}

# Sensible per-provider defaults; override via probe(... model=...) / CLI.
DEFAULT_CHAT_MODEL = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4.1-mini",
    "deepseek": "deepseek-v4-flash",
    "ollama": "llama3.1",
}
DEFAULT_EMBED_MODEL = {
    "openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
}
OLLAMA_HOST = "http://localhost:11434"

# Adaptive thinking is only accepted on some Anthropic models (Haiku rejects it).
_ADAPTIVE = ("claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
             "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable-5")


@dataclass
class LLMResult:
    text: str
    usage: Usage


# --- chat --------------------------------------------------------------------


def chat(provider: str, model: str, system: str, user: str,
         *, schema: dict | None = None, host: str = OLLAMA_HOST,
         max_tokens: int = 1500) -> LLMResult:
    """One schema-constrained generation turn. Returns text (JSON if `schema`) + usage."""
    if provider == "anthropic":
        return _chat_anthropic(model, system, user, schema, max_tokens)
    if provider in OPENAI_COMPATIBLE:
        return _chat_openai_compatible(provider, model, system, user, schema, max_tokens)
    if provider == "ollama":
        return _chat_ollama(model, system, user, schema, host)
    raise ValueError(f"unknown provider {provider!r}; choose one of {PROVIDERS}")


def _chat_anthropic(model, system, user, schema, max_tokens) -> LLMResult:
    import anthropic

    client = anthropic.Anthropic()
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}])
    if schema is not None:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    if any(model.startswith(m) for m in _ADAPTIVE):
        kwargs["thinking"] = {"type": "adaptive"}
    resp = client.messages.create(**kwargs)
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    return LLMResult(text, Usage.from_response(getattr(resp, "model", model) or model, resp.usage))


#: OpenAI model families whose token cap is `max_completion_tokens`: the cap has
#: to cover the reasoning as well as the reply, so the parameter was renamed.
_COMPLETION_TOKENS_MODELS = ("gpt-5", "o1", "o3", "o4")


def _openai_request(provider, model, system, user, schema, max_tokens,
                    json_mode: bool = True) -> dict:
    """The chat-completions request body, shaped for what the endpoint supports.

    `json_mode=False` keeps the schema in the prompt but omits `response_format`,
    which is the escape hatch when an endpoint's JSON mode returns nothing.
    """
    spec = OPENAI_COMPATIBLE[provider]
    # The reasoning-era families reject `max_tokens` outright ("Unsupported
    # parameter ... use 'max_completion_tokens' instead", a 400 before any work).
    # Matched by PREFIX so a dated id (gpt-5.4-nano-2026-03-17) resolves too —
    # the same rule numagent's OpenAI client applies.
    cap = ("max_completion_tokens" if model.startswith(_COMPLETION_TOKENS_MODELS)
           else "max_tokens")
    body = {"model": model, cap: max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    if schema is not None:
        if spec["json_schema"]:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": False},
            }
        else:
            # JSON mode only: the schema travels in the prompt instead, and the
            # word "json" must appear there or the endpoint rejects the mode.
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            body["messages"][-1]["content"] += (
                "\n\nReturn a single JSON object matching this JSON Schema exactly "
                "— every required key present, no extra keys, no prose, no code "
                "fence. Keep every string field under 400 characters so the object "
                "closes:\n" + json.dumps(schema))
    return body


def _usage_of(model: str, u: dict, spec: dict) -> Usage:
    """The usage record for one OpenAI-compatible response.

    DeepSeek reports the cache split and prices by time of day, so both are
    recorded at call time — the window cannot be recovered from the numbers
    afterwards, and a run that straddles 04:00 UTC would be mispriced.
    """
    from .costs import is_peak

    hit = u.get("prompt_cache_hit_tokens") or 0
    miss = u.get("prompt_cache_miss_tokens")
    # `input_tokens` stays the UNCACHED input, matching the Anthropic semantics
    # the rest of the accounting assumes; the cached subset is carried
    # separately and billed at its own (much lower) rate.
    return Usage(model=model,
                 input_tokens=(miss if miss is not None
                               else max((u.get("prompt_tokens", 0) or 0) - hit, 0)),
                 output_tokens=u.get("completion_tokens", 0) or 0,
                 cache_read_tokens=hit,
                 peak=is_peak() if spec.get("time_of_day_pricing") else None)



def _chat_openai_compatible(provider, model, system, user, schema, max_tokens,
                            timeout: float = 180.0, retry: bool = True,
                            json_mode: bool = True) -> LLMResult:
    """One turn against any endpoint speaking the OpenAI chat-completions API.

    A third-party endpoint is called over plain HTTP: it needs nothing the
    project does not already depend on, where the OpenAI SDK is an optional
    extra that a miner install does not pull in. OpenAI itself keeps the SDK,
    which owns its auth, base-URL and retry behaviour.
    """
    import os

    spec = OPENAI_COMPATIBLE[provider]
    key = os.environ.get(spec["api_key_env"])
    # a reasoning endpoint needs room for the thinking AND the answer
    budget = max_tokens * spec.get("budget_multiplier", 1)
    body = _openai_request(provider, model, system, user, schema, budget, json_mode)

    if spec["base_url"]:
        import requests

        if not key:
            raise RuntimeError(f"{spec['api_key_env']} is not set — required for "
                               f"--provider {provider}")
        resp = requests.post(f"{spec['base_url'].rstrip('/')}/chat/completions",
                             json=body, timeout=timeout,
                             headers={"Authorization": f"Bearer {key}",
                                      "Content-Type": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"{provider} API {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        # A reasoning model sometimes writes the answer while THINKING and then
        # emits nothing: `content` is empty and the finished object is sitting in
        # `reasoning_content`. It was paid for either way, so it is read rather
        # than thrown away — `extract_json` pulls the object out of the prose.
        if not text.strip():
            text = message.get("reasoning_content") or ""
        u = data.get("usage") or {}
        # A JSON object cut off at the token cap is not partially useful — it does
        # not parse at all, and the caller sees "could not parse JSON" for what is
        # really a budget problem. A verbose model hits this on a long rationale,
        # so the one retry doubles the room rather than discarding the answer.
        if retry and schema is not None:
            # Two failures that look alike and want OPPOSITE remedies.
            #
            # finish_reason="length": the object was cut off mid-write, so give
            # it more room.
            #
            # finish_reason="stop" with empty content: a reasoning model that
            # thought and then emitted nothing. More room does not help — it
            # measurably hurts, since a larger budget lets the reasoning wander
            # further before stopping. Retry at the SAME size, and drop JSON mode
            # (the schema stays in the prompt, and `extract_json` copes with a
            # fenced or prefaced object), which halves the reasoning spend.
            if choice.get("finish_reason") == "length":
                # A truncated object is now salvaged by `extract_json`, so this
                # retry is about getting a COMPLETE one; if the bigger budget is
                # also exhausted, the partial answer still beats nothing.
                bigger = _chat_openai_compatible(provider, model, system, user, schema,
                                                 max_tokens * 3, timeout=timeout, retry=False)
                return bigger if bigger.text.strip() else LLMResult(text, _usage_of(model, u, spec))
            if not text.strip():
                # Emitting nothing is intermittent, so the fallback is tried more
                # than once: first without JSON mode, then with an explicit final
                # instruction. Both keep the original budget.
                nudged = user + ("\n\nWrite the JSON object now. Output the object "
                                 "itself and nothing else — no explanation, no code fence.")
                brief = user + ("\n\nAnswer with the JSON object only. Keep every field "
                                "short — one or two sentences — and do not think at "
                                "length before writing it.")
                for prompt, mode in ((user, False), (nudged, False), (brief, False)):
                    out = _chat_openai_compatible(provider, model, system, prompt,
                                                  schema, max_tokens, timeout=timeout,
                                                  retry=False, json_mode=mode)
                    if out.text.strip():
                        return out
                return out
        # DeepSeek reports the cache split and prices by time of day, so both are
        # recorded at call time — the window cannot be recovered from the numbers
        # afterwards, and a run that straddles 04:00 UTC would be mispriced.
        return LLMResult(text, _usage_of(model, u, spec))

    import openai

    client = openai.OpenAI()
    resp = client.chat.completions.create(**body)
    text = resp.choices[0].message.content or ""
    u = resp.usage
    return LLMResult(text, Usage(model=model,
                                 input_tokens=getattr(u, "prompt_tokens", 0) or 0,
                                 output_tokens=getattr(u, "completion_tokens", 0) or 0))


def _chat_ollama(model, system, user, schema, host) -> LLMResult:
    import requests

    body = {"model": model, "stream": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    if schema is not None:
        body["format"] = schema  # Ollama accepts a JSON schema as the format
    r = requests.post(f"{host}/api/chat", json=body, timeout=300)
    r.raise_for_status()
    d = r.json()
    text = (d.get("message") or {}).get("content", "")
    usage = Usage(model=model,
                  input_tokens=int(d.get("prompt_eval_count", 0) or 0),
                  output_tokens=int(d.get("eval_count", 0) or 0))
    return LLMResult(text, usage)


def _close_json(prefix: str) -> str:
    """`prefix` with any open string and brackets closed — "" if nothing is open."""
    stack, in_string, escaped = [], False, False
    for ch in prefix:
        if escaped:
            escaped = False
        elif in_string:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if not stack:
        return ""
    out = prefix + ('"' if in_string else "")
    out = re.sub(r",\s*$", "", out.rstrip())
    out = re.sub(r',?\s*"[^"]*"\s*:\s*$', "", out)        # a key with no value yet
    return out + "".join(reversed(stack))


def close_truncated_json(text: str, attempts: int = 60):
    """Candidate repairs of an object the model was still writing, best first.

    A JSON object cut off mid-write does not parse at all, so the whole answer is
    normally thrown away — including the fields that WERE finished. Since a
    schema is written in order, what survives is the front of it: the summary and
    the input/output examples, which is most of what a strategy is for.

    Each candidate closes the structure at a progressively earlier boundary, so
    the first that parses keeps as much as could be kept. Only structure is ever
    added — a closing quote, the open brackets in reverse. Nothing is invented.
    """
    start = text.find("{")
    if start < 0:
        return
    body = text[start:]
    cuts = [len(body)]
    for i in range(len(body) - 1, 0, -1):
        if body[i] in ',}]':
            cuts.append(i + 1 if body[i] != "," else i)
            if len(cuts) >= attempts:
                break
    for cut in cuts:
        candidate = _close_json(body[:cut])
        if candidate:
            yield candidate


def extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from a model response."""
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)  # strip prose / code fences
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    for repaired in close_truncated_json(text):
        try:
            out = json.loads(repaired)
        except json.JSONDecodeError:
            continue
        if isinstance(out, dict) and out:
            out["_truncated"] = True         # the tail was never written
            return out
    raise ValueError(f"could not parse JSON from response: {text[:300]}")


# --- embeddings --------------------------------------------------------------


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.clip(n, 1e-12, None)


class Embedder:
    """fit(corpus) then encode(texts) -> L2-normalized row vectors."""

    def fit(self, corpus: list[str]) -> "Embedder":
        return self

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


class OpenAIEmbedder(Embedder):
    def __init__(self, model: str):
        self.model = model

    def encode(self, texts):
        import openai

        client = openai.OpenAI()
        resp = client.embeddings.create(model=self.model, input=list(texts))
        return _l2(np.array([d.embedding for d in resp.data], dtype=np.float32))


class OllamaEmbedder(Embedder):
    def __init__(self, model: str, host: str = OLLAMA_HOST):
        self.model, self.host = model, host

    def encode(self, texts):
        import requests

        r = requests.post(f"{self.host}/api/embed",
                          json={"model": self.model, "input": list(texts)}, timeout=120)
        r.raise_for_status()
        return _l2(np.array(r.json()["embeddings"], dtype=np.float32))


class TfidfEmbedder(Embedder):
    """Self-contained TF-IDF (no torch, no network) — the offline/anthropic path."""

    _TOKEN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*|\d+")

    def __init__(self):
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray | None = None

    def _toks(self, s: str) -> list[str]:
        return [t.lower() for t in self._TOKEN.findall(s or "")]

    def fit(self, corpus):
        df: dict[str, int] = {}
        for doc in corpus:
            for t in set(self._toks(doc)):
                df[t] = df.get(t, 0) + 1
        self.vocab = {t: i for i, t in enumerate(sorted(df))}
        n = max(1, len(corpus))
        self.idf = np.ones(len(self.vocab), dtype=np.float32)
        for t, i in self.vocab.items():
            self.idf[i] = math.log((1 + n) / (1 + df[t])) + 1.0
        return self

    def encode(self, texts):
        rows = np.zeros((len(texts), len(self.vocab)), dtype=np.float32)
        for r, doc in enumerate(texts):
            for t in self._toks(doc):
                j = self.vocab.get(t)
                if j is not None:
                    rows[r, j] += 1.0
        if self.idf is not None:
            rows *= self.idf
        return _l2(rows)


def make_embedder(provider: str, model: str | None = None,
                  host: str = OLLAMA_HOST) -> Embedder:
    """Provider-matched embedder with local fallback.

    openai -> OpenAI embeddings; ollama -> Ollama embeddings; anthropic and
    deepseek (neither publishes an embedding API) / anything else ->
    sentence-transformers if importable, else a self-contained TF-IDF embedder.
    Pair those generation providers with ``--embed-provider ollama`` for dense
    retrieval; the fallback keeps a run working, not equally well.
    """
    if provider == "openai":
        return OpenAIEmbedder(model or DEFAULT_EMBED_MODEL["openai"])
    if provider == "ollama":
        return OllamaEmbedder(model or DEFAULT_EMBED_MODEL["ollama"], host)
    # anthropic / offline
    try:
        import sentence_transformers  # noqa: F401
        return _STEmbedder(model or "sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        return TfidfEmbedder()


class _STEmbedder(Embedder):  # used only when sentence-transformers is installed
    def __init__(self, name: str):
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(name)

    def encode(self, texts):
        v = self.model.encode(list(texts), convert_to_numpy=True, normalize_embeddings=True)
        return v.astype(np.float32)

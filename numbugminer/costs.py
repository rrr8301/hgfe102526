from __future__ import annotations

from dataclasses import dataclass

# USD per 1M tokens (input, output). Keep in sync with the Anthropic pricing page.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-fable-5": (10.00, 50.00),
    # DeepSeek, via its OpenAI-compatible API. DeepSeek prices by time of day and
    # by cache status; the table carries the PEAK CACHE-MISS rate so a single
    # number is always an upper bound on what a run was billed. The exact figure
    # is available too — see `cost_breakdown`.
    "deepseek-v4-flash": (0.44, 1.32),
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    # OpenAI, same rates numagent's agent prices its runs at
    # (numagent/agent/llm/base.py). Without them a gpt-* run reports $0.00 spent,
    # which reads as "free" rather than "unpriced".
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4": (2.50, 15.00),
    "gpt-5.1": (1.25, 10.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}

#: Off-peak is a flat halving of every DeepSeek rate, so one multiplier covers
#: input, cached input and output alike.
OFF_PEAK_MULT: dict[str, float] = {"deepseek-v4-flash": 0.5}

#: Cached input as a fraction of the cache-miss input rate ($0.014 vs $0.44).
#: Model-specific because it differs sharply from Anthropic's ~0.1.
CACHE_READ_MULT_BY_MODEL: dict[str, float] = {"deepseek-v4-flash": 0.014 / 0.44}

#: DeepSeek peak windows, UTC, Monday-Friday. Everything else is off-peak.
PEAK_WINDOWS_UTC: tuple = ((1, 4), (6, 10))


def is_peak(when=None) -> bool:
    """Whether `when` (UTC, default now) falls in a peak-price window."""
    from datetime import datetime, timezone

    when = when or datetime.now(timezone.utc)
    if when.weekday() >= 5:                       # Saturday, Sunday
        return False
    hour = when.hour + when.minute / 60
    return any(start <= hour < end for start, end in PEAK_WINDOWS_UTC)


def _model_key(model: str) -> str | None:
    """The pricing-table key `model` resolves to, if any."""
    if model in MODEL_PRICING:
        return model
    matches = [k for k in MODEL_PRICING if model.startswith(k)]
    return max(matches, key=len) if matches else None
CACHE_READ_MULT = 0.1    # cache-read tokens bill at ~0.1x the input rate
CACHE_WRITE_MULT = 1.25  # 5-minute ephemeral cache writes bill at ~1.25x


def _rates(model: str) -> tuple[float, float] | None:
    """(input, output) $/1M for `model`, tolerating suffixes like '[1m]'/'-fast'."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Longest known model id that prefixes the (possibly suffixed) id wins.
    matches = [k for k in MODEL_PRICING if model.startswith(k)]
    return MODEL_PRICING[max(matches, key=len)] if matches else None


@dataclass(frozen=True)
class Usage:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_response(cls, model: str, u) -> "Usage":
        """Build from an Anthropic response `usage` object (fields fail soft to 0)."""
        g = lambda name: int(getattr(u, name, 0) or 0)  # noqa: E731
        return cls(
            model=model,
            input_tokens=g("input_tokens"),
            output_tokens=g("output_tokens"),
            cache_read_tokens=g("cache_read_input_tokens"),
            cache_write_tokens=g("cache_creation_input_tokens"),
        )

    #: True/False when the call is known to have happened in (or out of) a peak
    #: window; None when the provider does not price by time of day.
    peak: bool | None = None

    def _cost(self, peak: bool = True) -> float:
        rates = _rates(self.model)
        if not rates:
            return 0.0
        key = _model_key(self.model) or ""
        window = 1.0 if peak else OFF_PEAK_MULT.get(key, 1.0)
        inp, out = rates[0] / 1e6 * window, rates[1] / 1e6 * window
        cache_mult = CACHE_READ_MULT_BY_MODEL.get(key, CACHE_READ_MULT)
        return round(
            self.input_tokens * inp
            + self.output_tokens * out
            + self.cache_read_tokens * inp * cache_mult
            + self.cache_write_tokens * inp * CACHE_WRITE_MULT, 6)

    @property
    def cost_usd(self) -> float:
        """The conservative figure: peak rates, so it never understates a bill."""
        return self._cost(peak=True)

    @property
    def cost_usd_billed(self) -> float:
        """The rate window the call actually landed in, when that is known."""
        return self._cost(peak=True if self.peak is None else self.peak)

    @property
    def prompt_tokens(self) -> int:
        """Every input token the call sent, cached or not — what a token count
        means to a reader, as opposed to what each part is billed at."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def cost_breakdown(self) -> dict:
        """Both figures plus what they were computed from.

        A provider that prices by time of day and cache status cannot be reduced
        to one number without saying which one it is: the upper bound is what a
        paper should quote, and the windowed figure is what the account was
        charged.
        """
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "peak_window": self.peak,
            "cost_usd_upper_bound": self.cost_usd,      # peak, cache-miss rates
            "cost_usd_billed_estimate": self.cost_usd_billed,
        }

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": self.cost_usd,
        }

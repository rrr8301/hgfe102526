from __future__ import annotations

import math
import struct

# (pack code, unsigned-int unpack code, bit width) per IEEE dtype.
_DTYPE_FMT = {
    "float16": ("e", "H", 16),
    "float32": ("f", "I", 32),
    "float64": ("d", "Q", 64),
}


def _coerce(value) -> float:
    return float(value.strip()) if isinstance(value, str) else float(value)


def _ordered_int(value: float, dtype: str) -> int:
    """Map a float to a sign-magnitude-ordered integer so adjacent floats differ
    by 1 — the basis for a ULP distance (handles the sign discontinuity)."""
    pack, uint, nbits = _DTYPE_FMT[dtype]
    u = struct.unpack(uint, struct.pack(pack, value))[0]
    sign = 1 << (nbits - 1)
    return (sign - u) if (u & sign) else u  # negatives map below 0; ±0 -> 0


def compute_ulp_distance(expected, observed, dtype: str = "float64") -> dict:
    """Distance in units in the last place (ULP) for `dtype`: how many
    representable floats lie between the two values. 0 == bit-identical,
    1 == adjacent floats. The scale-free way to say "how wrong" a rounding is."""
    e, o = _coerce(expected), _coerce(observed)
    if dtype not in _DTYPE_FMT:
        dtype = "float64"
    if math.isnan(e) or math.isnan(o):
        return {"ulp_distance": None, "note": "NaN operand — ULP distance undefined", "dtype": dtype}
    if math.isinf(e) or math.isinf(o):
        same = math.isinf(e) and math.isinf(o) and (e > 0) == (o > 0)
        return {"ulp_distance": 0 if same else None, "dtype": dtype,
                "note": "matching infinity" if same else "infinity vs finite/other — ULP undefined"}
    ulps = abs(_ordered_int(e, dtype) - _ordered_int(o, dtype))
    return {"ulp_distance": ulps, "dtype": dtype, "expected": e, "observed": o}


def significant_digits_agreement(expected, observed) -> dict:
    """Number of correct significant decimal digits ≈ -log10(relative error).

    A drop from ~16 (double) toward 0 is the fingerprint of accuracy loss /
    catastrophic cancellation."""
    e, o = _coerce(expected), _coerce(observed)
    if not (math.isfinite(e) and math.isfinite(o)):
        return {"correct_significant_digits": None, "note": "non-finite operand"}
    if e == o:
        return {"correct_significant_digits": float("inf"), "relative_error": 0.0}
    if e == 0.0:
        return {"correct_significant_digits": None, "note": "expected is 0"}
    rel = abs(o - e) / abs(e)
    digits = -math.log10(rel) if rel > 0 else float("inf")
    return {"correct_significant_digits": max(0.0, digits), "relative_error": rel}

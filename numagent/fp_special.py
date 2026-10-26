from __future__ import annotations

import math

# Per-dtype limits (IEEE-754 binary16/32/64). smallest_normal is the boundary
# below which a nonzero finite value is subnormal; smallest_subnormal is the
# least positive representable value.
FLOAT_LIMITS = {
    "float16": {"smallest_normal": 6.103515625e-05, "smallest_subnormal": 5.960464477539063e-08,
                "max": 65504.0, "eps": 0.0009765625, "mant_bits": 10, "exp_bits": 5},
    "float32": {"smallest_normal": 1.1754943508222875e-38, "smallest_subnormal": 1.401298464324817e-45,
                "max": 3.4028234663852886e38, "eps": 1.1920928955078125e-07, "mant_bits": 23, "exp_bits": 8},
    "float64": {"smallest_normal": 2.2250738585072014e-308, "smallest_subnormal": 5e-324,
                "max": 1.7976931348623157e308, "eps": 2.220446049250313e-16, "mant_bits": 52, "exp_bits": 11},
}


def _coerce(value) -> float:
    """Accept a float or a string like 'nan', 'inf', '-inf', '-0.0'."""
    if isinstance(value, str):
        return float(value.strip())
    return float(value)


def _signbit(v: float) -> bool:
    return math.copysign(1.0, v) < 0.0


def classify_float(value, dtype: str = "float64") -> dict:
    """Classify a value into its IEEE-754 category for `dtype`.

    category ∈ {nan, +inf, -inf, zero, -zero, subnormal, normal}.
    """
    v = _coerce(value)
    lim = FLOAT_LIMITS.get(dtype, FLOAT_LIMITS["float64"])
    if math.isnan(v):
        cat = "nan"
    elif math.isinf(v):
        cat = "+inf" if v > 0 else "-inf"
    elif v == 0.0:
        cat = "-zero" if _signbit(v) else "zero"
    elif abs(v) < lim["smallest_normal"]:
        cat = "subnormal"
    else:
        cat = "normal"
    return {
        "value": v if math.isfinite(v) else repr(v),
        "dtype": dtype,
        "category": cat,
        "signbit": _signbit(v),
        "is_finite": math.isfinite(v),
        "hex": v.hex() if math.isfinite(v) else None,
    }


def compare_special_values(expected, observed, dtype: str = "float64",
                           rtol: float = 1e-9, atol: float = 0.0) -> dict:
    """Compare two values with SPECIAL-VALUE-aware semantics.

    Unlike a bare ==, this treats NaN vs NaN as a match, distinguishes +0.0 from
    -0.0 (a lost sign bit is a real bug), requires matching Inf signs, and only
    falls back to a tolerance check for ordinary finite values.
    """
    e, o = _coerce(expected), _coerce(observed)
    ec = classify_float(e, dtype)["category"]
    oc = classify_float(o, dtype)["category"]
    equal = None
    note = ""
    if math.isnan(e) or math.isnan(o):
        equal = math.isnan(e) and math.isnan(o)
        note = "both NaN" if equal else "NaN vs non-NaN"
    elif math.isinf(e) or math.isinf(o):
        equal = math.isinf(e) and math.isinf(o) and (e > 0) == (o > 0)
        note = "matching infinity" if equal else "infinity mismatch (presence or sign)"
    elif e == 0.0 and o == 0.0:
        equal = _signbit(e) == _signbit(o)
        note = "identical signed zero" if equal else "signed-zero mismatch (+0.0 vs -0.0)"
    else:
        abs_err = abs(o - e)
        tol = atol + rtol * abs(e)
        equal = abs_err <= tol
        note = f"finite compare: abs_err={abs_err:.3e}, tol={tol:.3e}"
    return {"equal": bool(equal), "expected_class": ec, "observed_class": oc,
            "expected_signbit": _signbit(e), "observed_signbit": _signbit(o), "note": note}

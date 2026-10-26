from __future__ import annotations

from dataclasses import dataclass, field

#: the annotated categories, in the benchmark's own tokens
CATEGORIES = ("accuracy", "special_value", "correctness", "convergence")

_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_NULLABLE = {"type": ["string", "null"]}


def strict_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            node.setdefault("additionalProperties", False)
        for value in node.values():
            strict_objects(value)
    elif isinstance(node, list):
        for item in node:
            strict_objects(item)
    return node


def _obj(props: dict, required: list[str]) -> dict:
    # strict at construction, so every question schema is portable to the
    # structured-output path without each call site remembering to convert it
    return strict_objects(
        {"type": "object", "properties": props, "required": required})


#: DP1 — the vocabulary that makes retrieval numerical-aware rather than generic.
#: Appended to every question's query so the embedder is pulled towards passages
#: about numerical behaviour, not towards the project's prose in general.
NUMERICAL_TERMS = (
    "floating point precision rounding dtype numerical stability tolerance "
    "overflow underflow NaN infinity signed zero subnormal convergence residual "
    "iteration epsilon significant digits relative error absolute error"
)

#: DP3 — category-conditioned query expansion. The annotated category selects
#: the extra terms, so Q3 retrieves what that kind of defect is made of.
CATEGORY_TERMS = {
    "accuracy": "dtype precision rounding cancellation catastrophic tolerance rtol atol "
                "magnitude significant digits ulp float32 float64 longdouble promotion",
    "special_value": "NaN infinity inf signed zero negative zero subnormal denormal "
                     "isfinite isnan masked sentinel NaT overflow underflow guard",
    "correctness": "formula invariant derivation algorithm expected value identity "
                   "mathematical property sign convention index order axis dtype shape",
    "convergence": "initialization initial guess residual stopping criterion tolerance "
                   "max iterations diverges oscillates step size damping ill-conditioned",
}

#: What each category additionally demands of a reproduction, stated to the model
#: so Q3's answer is actionable rather than descriptive.
CATEGORY_FOCUS = {
    "accuracy": "the dtype and precision the discrepancy depends on, the magnitude "
                "regime where it appears, and the tolerance at which correct and "
                "incorrect answers separate",
    "special_value": "which special values are involved (NaN, +/-inf, signed zero, "
                     "subnormals), where they enter, and what the correct handling is",
    "correctness": "the mathematical property or formula that is violated, and the "
                   "concrete values that demonstrate the violation",
    "convergence": "the initialisation, the stopping criterion, the tolerance and the "
                   "iteration count at which the behaviour differs",
}


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    query: str                       # the retrieval query, before expansion
    sources: tuple                   # chunk source types this question draws on
    schema: dict
    k: int = 8

    def retrieval_query(self, category: str = "") -> str:
        """The query actually sent to the index: the question's own terms, the
        numerical vocabulary, and — for Q3 — the category's expansion (DP1/DP3)."""
        parts = [self.query, NUMERICAL_TERMS]
        if self.id == "characteristics" and category:
            parts.append(CATEGORY_TERMS.get(category, ""))
        return " ".join(p for p in parts if p)


# --------------------------------------------------------------------------- #
# Q1 — numerical environment (HBEC)
# --------------------------------------------------------------------------- #
ENVIRONMENT = Question(
    id="environment",
    text=(
        "What historical software environment, numerical dependencies, configuration "
        "and execution procedure are required to build this revision and run its "
        "tests so that the reported numerical behaviour can be observed?\n"
        "Report the Python (or language) version, the dependencies and their version "
        "constraints, native/BLAS/LAPACK or other numerical backends, build and "
        "installation commands, and the command that runs the tests.\n"
        "Separate what the repository or PR DOCUMENTS from what you are inferring. "
        "Leave a field null when the evidence does not state it — do not guess a "
        "version number."
    ),
    query=("python version dependencies requirements install build command run tests "
           "pytest BLAS LAPACK compiler environment setup numerical backend"),
    sources=("readme", "config", "pr_body", "issue", "commit", "diff"),
    schema=_obj({
        "language": _NULLABLE,
        "language_version": _NULLABLE,
        "dependencies": {"type": "array", "items": _obj(
            {"name": _STR, "constraint": _NULLABLE, "documented": {"type": "boolean"},
             "numerically_relevant": {"type": "boolean"}, "source": _STR},
            ["name", "documented", "source"])},
        "numerical_backend": _NULLABLE,
        "installation_commands": _STRS,
        "build_commands": _STRS,
        "test_commands": _STRS,
        "system_packages": _STRS,
        "era_notes": _STRS,
        "confidence": {"type": "number"},
    }, ["dependencies", "installation_commands", "test_commands", "confidence"]),
)

# --------------------------------------------------------------------------- #
# Q2 — numerical failure (BRTG)
# --------------------------------------------------------------------------- #
FAILURE = Question(
    id="failure",
    text=(
        "What incorrect NUMERICAL behaviour does this bug produce, what should happen "
        "instead, and under which inputs or execution conditions does the discrepancy "
        "appear?\n"
        "Name the affected API as an IMPORTABLE DOTTED PATH wherever the evidence "
        "allows it — `numpy.f2py.capi_maps.c2capi_map`, not \"the f2py C-type to "
        "dtype mapping\" and not the bare name `c2capi_map`. A reader who cannot "
        "import the symbol cannot test it: a generated test guessed "
        "`cfuncs.c2capi_map` and `_get_c2capi_map()` and failed on both revisions "
        "without ever reaching the arithmetic. Give the module path even when the "
        "evidence only names the symbol, if the repository makes it unambiguous.\n"
        "Name the affected API precisely. Quote CONCRETE values wherever the evidence "
        "gives them — the input, the wrong output, the expected output — because those "
        "values are the oracle a reproduction is checked against. Record the form the "
        "failure takes (a wrong value, a raised exception, a warning, a hang) and any "
        "clue about how correctness is judged (an assertion, a tolerance, a reference "
        "implementation).\n"
        "For every input/output example, set `value_kind` to say WHAT the expected "
        "output is: a `float` or `array` compared at a tolerance, an `integer` or "
        "`count` compared exactly, a `string` compared as text (a dtype name or enum "
        "member is a string, not a numeric constant), a `boolean`, an `exception` "
        "type, or a `property` that holds without a single expected value. A reader "
        "that mistakes a string for a number writes an assertion that cannot run.\n"
        "Leave a field null rather than inventing a value the evidence does not give."
    ),
    query=("incorrect result wrong value expected value reproduce steps triggering input "
           "error message traceback assertion failure observed output"),
    sources=("issue", "pr_body", "discussion", "diff", "commit"),
    schema=_obj({
        "affected_api": _NULLABLE,
        "observed_behavior": _NULLABLE,
        "expected_behavior": _NULLABLE,
        "manifestation": {"type": "string",
                          "enum": ["wrong_value", "exception", "warning", "hang",
                                   "crash", "unknown"]},
        "trigger_conditions": _STRS,
        # `value_kind` types the expected output, because the comparison it
        # calls for differs completely. numpy#31199's expected output is the
        # STRING "NPY_CLONGDOUBLE" in a dict; the generated test wrote
        # `== np.NPY_CLONGDOUBLE`, a numpy attribute that does not exist, and
        # failed on both revisions. scikit-learn#31856's is an iteration COUNT
        # (`n_iter_ > 6`), not a value to compare at a tolerance. Untyped, both
        # read as "a number", which is the one thing they are not.
        "input_output_examples": {"type": "array", "items": _obj(
            {"input": _STR, "buggy_output": _STR, "expected_output": _STR,
             "value_kind": {"type": "string",
                            "enum": ["float", "array", "integer", "count",
                                     "string", "boolean", "exception",
                                     "property", "unknown"]},
             "source": _STR}, ["input", "buggy_output", "expected_output"])},
        "oracle_clues": _STRS,
        "confidence": {"type": "number"},
    }, ["observed_behavior", "expected_behavior", "manifestation",
        "trigger_conditions", "confidence"]),
)

# --------------------------------------------------------------------------- #
# Q3 — category-specific numerical evidence
# --------------------------------------------------------------------------- #
CHARACTERISTICS = Question(
    id="characteristics",
    text=(
        "This bug is annotated as a {category} bug. What additional numerical "
        "properties must a reproduction account for?\n"
        "Specifically: {focus}.\n"
        "Also record the dtype, the precision, the magnitude regime and the tolerance "
        "wherever the evidence states them, and list the category-specific evidence "
        "you found. Leave a field null when the evidence is silent."
    ),
    query=("dtype precision tolerance magnitude numerical property special value "
           "convergence criterion invariant"),
    sources=("issue", "pr_body", "discussion", "diff", "test_diff", "commit"),
    schema=_obj({
        "dtype": _NULLABLE,
        "precision": _NULLABLE,
        "magnitude": _NULLABLE,
        "tolerance": _NULLABLE,
        "category_specific_evidence": _STRS,
        "reproduction_risks": _STRS,
        "confidence": {"type": "number"},
    }, ["category_specific_evidence", "confidence"]),
)

QUESTIONS: tuple[Question, ...] = (ENVIRONMENT, FAILURE, CHARACTERISTICS)


def render(q: Question, category: str = "") -> str:
    """The question text with the category placeholders filled in."""
    if q.id != "characteristics":
        return q.text
    return q.text.format(category=(category or "numerical").replace("_", " "),
                         focus=CATEGORY_FOCUS.get(category,
                                                  "the numerical properties the "
                                                  "evidence emphasises"))

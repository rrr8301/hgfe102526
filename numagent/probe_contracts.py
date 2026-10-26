from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from .repro_probe_langs import CODE_CONTRACT, canonical


@dataclass
class ValidationResult:
    valid: bool
    errors: list = field(default_factory=list)
    detected_language: str = ""      # what the code looks like, when it is wrong

    def feedback(self, language: str) -> str:
        """The message the agent gets back, in the shape it can act on."""
        lines = ["PROBE_GENERATION_ERROR", f"Requested language: {language}", "", "Problems:"]
        lines += [f"  - {e}" for e in self.errors]
        if self.detected_language:
            lines.append(f"  - the code looks like {self.detected_language}.")
        lines += ["", f"The {language} probe contract: {CODE_CONTRACT.get(language, '')}",
                  "", "Regenerate only the probe, following that contract."]
        return "\n".join(lines)


#: Signatures of the language a probe was actually written in.
_SIGNATURES = (
    ("Python", re.compile(r"^\s*(?:def\s+\w+\s*\(|from\s+[\w.]+\s+import\b|import\s+\w+"
                          r"|print\s*\(|#\s)", re.M)),
    ("Rust", re.compile(r"\bfn\s+\w+\s*\(|\blet\s+mut\b|::<|&\[")),
    ("Go", re.compile(r"\bfunc\s+\w+\s*\(|:=")),
    ("Java", re.compile(r"\bpublic\s+(?:static\s+)?(?:void|class)\b|\bSystem\.out\b")),
    ("JavaScript/TypeScript", re.compile(r"\b(?:const|let)\s+\w+\s*=|=>|\brequire\s*\(")),
)


def _looks_like(code: str, exclude: str) -> str:
    """Which language this code resembles, ignoring the one that was asked for."""
    for name, pattern in _SIGNATURES:
        if name.lower().startswith(exclude.lower()) or exclude.lower() in name.lower():
            continue
        if pattern.search(code or ""):
            return name
    return ""


def _records_via(code: str, *calls: str) -> bool:
    """Does the code call any of these recording functions? Whitespace-tolerant,
    because `probe ("x", v)` is the same call."""
    text = code or ""
    return any(re.search(re.escape(call[:-1]) + r"\s*\(", text) for call in calls)


#: Everything the Python harness defines. A probe that calls anything else in
#: this family invented it: `probe_result`, `probe_value`, `record`. The name
#: looks plausible, the call raises NameError at run time, and a container
#: round-trip is spent finding that out.
_PYTHON_API = {"probe", "probe_iteration", "probe_exception"}
_JS_API = {"probe", "probeIteration", "probe_iteration"}
_PROBE_CALL = re.compile(r"\b(probe[A-Za-z_]*)\s*\(")


def _invented_api(code: str, known: set) -> list:
    """Calls to probe-family names the harness does not define."""
    used = {m for m in _PROBE_CALL.findall(code or "")}
    return sorted(used - known)



# --------------------------------------------------------------------------- #
# the contracts
# --------------------------------------------------------------------------- #
_RECORDERS = frozenset({"probe", "probe_iteration", "probe_exception"})


def _reaches_recorder(code: str):
    """Does RUNNING this module reach a probe(...) call? None if it does not parse.

    "probe(" appearing in the text is not enough: a probe that defines
    `run_case()` — which records — and never calls it runs to completion,
    records nothing, and reads as a revision with nothing to say. Execution is
    followed from what runs at import (statements, decorators, defaults, class
    bodies, a __main__ block) through every function that is called OR merely
    referenced there — `map(run_case, …)`, `jax.vmap(f)` and callbacks count —
    so only a recorder nothing ever leads to is flagged.
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return None
    functions: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.setdefault(node.name, []).append(node)

    def names_in(nodes, into: set) -> None:
        """Every name used by code that runs when `nodes` run — not inside the
        bodies of functions they merely define."""
        stack = list(nodes)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                stack.extend(node.decorator_list)             # run at definition
                stack.extend(d for d in node.args.defaults + node.args.kw_defaults if d)
                continue
            if isinstance(node, ast.Lambda):
                stack.append(node.body)                       # usually called in place
                continue
            if isinstance(node, ast.Name):
                into.add(node.id)
            elif isinstance(node, ast.Attribute):
                into.add(node.attr)
            stack.extend(ast.iter_child_nodes(node))

    reached: set = set()
    names_in(tree.body, reached)
    seen: set = set()
    frontier = [n for n in reached if n in functions]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        found: set = set()
        for fn in functions[name]:
            names_in(fn.body, found)
        reached |= found
        frontier.extend(n for n in found if n in functions and n not in seen)
    return bool(reached & _RECORDERS)


def validate_python_probe(code: str, **_) -> ValidationResult:
    errors = []
    try:
        ast.parse(code or "")           # a real syntax check, and it costs nothing
    except SyntaxError as e:
        errors.append(f"the code is not valid Python: {e.msg} (line {e.lineno}).")
    if not _records_via(code, "probe(", "probe_iteration(", "probe_exception("):
        errors.append("record at least one observation with `probe(name, value)`.")
    elif _reaches_recorder(code) is False:
        errors.append("nothing that runs ever reaches `probe(...)`: the code defines "
                      "functions that record, but no top-level statement calls them, so the "
                      "probe would finish having recorded nothing. Call them at the top "
                      "level (or record there directly).")
    for invented in _invented_api(code, _PYTHON_API):
        errors.append(f"`{invented}` does not exist — the harness defines only "
                      f"probe(name, value), probe_iteration(**fields) and "
                      f"probe_exception(name, exception).")
    detected = _looks_like(code, "python") if errors else ""
    return ValidationResult(not errors, errors, detected)


def _validate_node_probe(code: str, module_style: str = "", **_) -> ValidationResult:
    errors = []
    if not _records_via(code, "probe(", "probeIteration(", "probe_iteration("):
        errors.append("record at least one observation with `probe(name, value)`.")
    for invented in _invented_api(code, _JS_API):
        errors.append(f"`{invented}` does not exist — the harness defines only "
                      f"probe(name, value) and probeIteration({{...}}).")
    if module_style == "esm" and re.search(r"\brequire\s*\(", code or ""):
        errors.append("this package is ESM (\"type\": \"module\"), so `require(...)` fails at "
                      "run time — import the module instead.")
    if module_style == "commonjs" and re.search(r"^\s*import\s+[\w{*]", code or "", re.M):
        errors.append("this package is CommonJS, so a static `import` fails — use "
                      "`const x = require('./path.js')`.")
    if re.search(r"\bfn\s+body\s*\(|\bfunc\s+Body\s*\(", code or ""):
        errors.append("the code defines a Rust/Go entry point; a JS probe is a plain module.")
    detected = _looks_like(code, "javascript") if errors else ""
    return ValidationResult(not errors, errors, detected)


validate_javascript_probe = _validate_node_probe
validate_typescript_probe = _validate_node_probe


def validate_rust_probe(code: str, **_) -> ValidationResult:
    errors = []
    if not re.search(r"\bfn\s+body\s*\(", code or ""):
        errors.append("a Rust probe must define `fn body()` — the harness calls it.")
    if re.search(r"\bfn\s+main\s*\(", code or ""):
        errors.append("do not define `fn main()`; the harness owns the entry point.")
    if not _records_via(code, "probe_scalar(", "probe_values(", "probe_text(", "probe_iteration("):
        errors.append("record at least one observation with `probe_scalar(name, value)`.")
    detected = _looks_like(code, "rust") if errors else ""
    return ValidationResult(not errors, errors, detected)


def validate_go_probe(code: str, **_) -> ValidationResult:
    errors = []
    if not re.search(r"\bfunc\s+Body\s*\(", code or ""):
        errors.append("a Go probe must define `func Body()` — the harness calls it.")
    if re.search(r"\bfunc\s+main\s*\(", code or ""):
        errors.append("do not define `func main()`; the harness owns the entry point.")
    if not _records_via(code, "Probe(", "ProbeIteration("):
        errors.append("record at least one observation with `Probe(name, value)`.")
    detected = _looks_like(code, "go") if errors else ""
    return ValidationResult(not errors, errors, detected)


def validate_java_probe(code: str, **_) -> ValidationResult:
    errors = []
    if not re.search(r"\bvoid\s+body\s*\(", code or ""):
        errors.append("a Java probe must be a class `Body` with "
                      "`public static void body() throws Exception`.")
    if re.search(r"\bstatic\s+void\s+main\s*\(", code or ""):
        errors.append("do not define `main`; the harness owns the entry point.")
    if not _records_via(code, "NumagentProbe.probe", "probe("):
        errors.append("record at least one observation with `NumagentProbe.probe(name, value)`.")
    detected = _looks_like(code, "java") if errors else ""
    return ValidationResult(not errors, errors, detected)


def _validate_c_family(code: str, language: str = "c", **_) -> ValidationResult:
    errors = []
    if not re.search(r"\bmain\s*\(", code or ""):
        errors.append(f"a {language.upper()} probe is a complete program with `main`.")
    if not _records_via(code, "probe_scalar(", "probe_array(", "probe_text(", "probe_iter(",
                        "probe("):
        errors.append("record at least one observation with `probe_scalar(name, value)`.")
    if "#include \"numagent_probe.h\"" in (code or ""):
        errors.append("do not include numagent_probe.h; the harness includes it for you.")
    detected = _looks_like(code, "c") if errors else ""
    return ValidationResult(not errors, errors, detected)


def validate_c_probe(code: str, **kw) -> ValidationResult:
    return _validate_c_family(code, language="c", **kw)


def validate_cpp_probe(code: str, **kw) -> ValidationResult:
    return _validate_c_family(code, language="cpp", **kw)


#: The dispatcher. A language absent from here has no contract, and its probes
#: go straight to execution — the compiler is then the only check.
PROBE_CONTRACTS = {
    "python": validate_python_probe,
    "javascript": validate_javascript_probe,
    "typescript": validate_typescript_probe,
    "rust": validate_rust_probe,
    "go": validate_go_probe,
    "java": validate_java_probe,
    "c": validate_c_probe,
    "cpp": validate_cpp_probe,
}


def validate_probe(language: str, code: str, *, module_style: str = "") -> ValidationResult:
    """Check `code` against `language`'s probe contract."""
    contract = PROBE_CONTRACTS.get(canonical(language) or language)
    if contract is None:
        return ValidationResult(True)
    return contract(code, module_style=module_style)

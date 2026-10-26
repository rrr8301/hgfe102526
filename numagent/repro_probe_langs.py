from __future__ import annotations

import re

from dataclasses import dataclass, field

#: canonical language name -> accepted aliases
ALIASES = {
    "python": ("python", "py", "python3"),
    "javascript": ("javascript", "js", "node", "nodejs"),
    "typescript": ("typescript", "ts"),
    "java": ("java",),
    "c": ("c",),
    "cpp": ("cpp", "c++", "cxx", "cc"),
    "rust": ("rust", "rs"),
    "go": ("go", "golang"),
}

#: what each language's toolchain check needs on PATH
TOOLCHAIN = {"python": "python", "javascript": "node", "typescript": "node",
             "java": "javac", "c": "cc", "cpp": "g++", "rust": "cargo", "go": "go"}

NO_TOOLCHAIN = "NUMAGENT_NO_TOOLCHAIN"


def canonical(language: str) -> str:
    """Normalize a language name; '' / 'auto' / unknown all come back as ''."""
    name = (language or "").strip().lower()
    for canon, aliases in ALIASES.items():
        if name == canon or name in aliases:
            return canon
    return ""


@dataclass
class ProbePlan:
    """Everything needed to run one probe: dirs to create, files, the command."""
    language: str
    command: str
    files: dict = field(default_factory=dict)     # path -> text
    dirs: tuple = ()                              # created before the files land


# --------------------------------------------------------------------------- #
# language detection (one shell call, read on the host)
# --------------------------------------------------------------------------- #
#: printed by DETECT_COMMAND, most specific marker first
_DETECT_ORDER = (
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "java"),
    ("pyproject.toml", "python"),
    ("setup.py", "python"),
    ("tsconfig.json", "typescript"),
    ("package.json", "javascript"),
)

DETECT_COMMAND = (
    "for f in go.mod Cargo.toml pom.xml build.gradle build.gradle.kts "
    "pyproject.toml setup.py tsconfig.json package.json; do "
    "[ -f \"$f\" ] && echo \"FILE $f\"; done; "
    # C/C++ have no single manifest: count sources, deepest two levels is enough
    "echo \"CPP $(ls *.cpp *.cc *.cxx src/*.cpp src/*.cc 2>/dev/null | wc -l)\"; "
    "echo \"C $(ls *.c src/*.c 2>/dev/null | wc -l)\"; "
    # A tsconfig.json is not TypeScript SOURCE: stdlib ships thousands of .d.ts
    # declarations beside JavaScript, and a probe sent through ts-node there
    # fails to compile before it can measure anything.
    "echo \"TS $(ls *.ts src/*.ts src/*/*.ts lib/*.ts 2>/dev/null "
    "| grep -vc '\\.d\\.ts$')\""
)


def language_from_markers(output: str) -> str:
    """Read DETECT_COMMAND's output into a language name ('' when unsure)."""
    files, counts = set(), {"CPP": 0, "C": 0, "TS": 0}
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "FILE":
            files.add(parts[1])
        elif len(parts) == 2 and parts[0] in counts:
            counts[parts[0]] = int(parts[1]) if parts[1].isdigit() else 0
    for marker, lang in _DETECT_ORDER:
        if marker in files:
            # a tsconfig with no .ts source of its own is a JavaScript project
            # publishing type declarations — probe it as JavaScript
            if lang == "typescript" and not counts["TS"] and "package.json" in files:
                return "javascript"
            return lang
    if counts["CPP"]:
        return "cpp"
    if counts["C"]:
        return "c"
    return ""


# --------------------------------------------------------------------------- #
# 1. Python — the agent writes a complete script; API injected as builtins
# --------------------------------------------------------------------------- #
_PYTHON_HARNESS = r'''
import builtins, json, math, os, runpy, sys, traceback, warnings

# The harness lives in /tmp, so python put /tmp on sys.path. For a revision that
# runs FROM ITS SOURCE the working directory belongs there instead, the way
# `python script.py` in it would — that is how a probe sees this revision rather
# than some installed copy.
#
# A revision that had to be COMPILED is the opposite case: its build lives in
# `.venv`, and putting the source tree first shadows it with a half-package
# whose extension modules were never built ("No module named
# astropy.table._column_mixins"). The venv IS this revision, so it is left to
# answer the import.
if not os.path.isdir(os.path.join(os.getcwd(), ".venv")):
    sys.path.insert(0, os.getcwd())

_RECORDS, _ITERATIONS, _WARNINGS = {}, [], []


_REPEATED = "__numagent_repeated__"


def _record(name, value):
    """Store `value` under `name` — EVERY one of them when a name repeats.

    A probe that loops over cases and records each under one name (a grid of
    degrees and flags, a sweep of dtypes) kept only the LAST case — which can be
    the one case the bug never touches, so the two revisions agreed. Repeats
    are kept in order, as one sequence under the name.
    """
    key = str(name)
    if key not in _RECORDS:
        _RECORDS[key] = value
    elif isinstance(_RECORDS[key], dict) and _REPEATED in _RECORDS[key]:
        _RECORDS[key][_REPEATED].append(value)
    else:
        _RECORDS[key] = {_REPEATED: [_RECORDS[key], value]}


def probe(name, value):
    """Record a named observation (number, sequence, array, or mapping)."""
    _record(name, value)
    return value


def probe_iteration(**fields):
    """Record one iteration of a solver/loop (e.g. residual=..., error=...)."""
    _ITERATIONS.append(fields)


def probe_exception(name, exc):
    """Record that a scenario RAISED — and say whose fault it was.

    An exception is a real answer when it comes from the code under test ("the
    buggy revision raises instead of returning a value" is what many reports
    claim) and no answer at all when it comes from the probe itself calling an
    API wrongly. The two are indistinguishable once only the message survives,
    so the deepest frame's file is recorded with it: the comparator can then
    refuse to treat the probe's own mistakes as evidence about a revision.
    """
    frames = traceback.extract_tb(getattr(exc, "__traceback__", None)) or []
    origin = frames[-1].filename if frames else ""
    # THIS REVISION is whatever lives under the working directory: its source
    # tree, or the venv holding its build. The question is whether the revision
    # took part at ALL, not where the exception finally surfaced: astropy calls
    # getattr(numpy, "in1d") and the AttributeError is raised inside numpy —
    # the deepest frame is a dependency, but the revision is what made the call.
    # A probe that calls numpy directly has no revision frame anywhere.
    root = os.path.realpath(os.getcwd())

    def _inside(filename):
        where = os.path.realpath(filename) if filename else ""
        return (bool(where) and where.startswith(root + os.sep)
                and "_numagent" not in os.path.basename(where))

    from_repository = any(_inside(f.filename) for f in frames)
    _record(name, {
        "raised": type(exc).__name__,
        "message": str(exc)[:300],
        "origin": origin,
        "in_repository": from_repository,
    })
    return exc


builtins.probe = probe
builtins.probe_iteration = probe_iteration
builtins.probe_exception = probe_exception

warnings.simplefilter("always")


def _show(message, category, filename, lineno, file=None, line=None):
    _WARNINGS.append("%s: %s" % (getattr(category, "__name__", category), message))


warnings.showwarning = _show

try:
    import numpy as _np
    _np.seterr(all="warn")          # IEEE FP exceptions surface as RuntimeWarnings
except Exception:
    pass


def _plain(v, depth=0):
    """Best-effort JSON-able conversion; NaN/Inf become the strings json can't."""
    if depth > 5:
        return repr(v)[:200]
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, dict):
        return {str(k): _plain(x, depth + 1) for k, x in list(v.items())[:200]}
    if isinstance(v, complex):
        return {"real": _plain(v.real, depth + 1), "imag": _plain(v.imag, depth + 1)}
    if hasattr(v, "tolist"):
        try:
            return _plain(v.tolist(), depth + 1)
        except Exception:
            pass
    if isinstance(v, (list, tuple, set, frozenset)):
        return [_plain(x, depth + 1) for x in list(v)[:500]]
    try:
        return _plain(float(v), depth + 1)
    except Exception:
        pass
    if hasattr(v, "evalf"):          # a sympy expression that is numeric after all
        try:
            return _plain(float(v.evalf()), depth + 1)
        except Exception:
            pass
    return repr(v)[:200]


# The probe runs in two stages so a failure can say WHERE it happened: loading
# the modules it needs, or running the code under test. A numerical pathology
# can only be inferred once the target operation was actually reached, so the
# stage is reported and the verifier reads it instead of guessing from the
# exception's name.
_stage, _status, _tb, _exc = "module_loading", "ok", "", ""
try:
    import ast as _ast
    with open("__PROBE_PATH__") as _fh:
        _src = _fh.read()
    _tree = _ast.parse(_src, "__PROBE_PATH__")
    _lead, _rest, _in_lead = [], [], True
    for _node in _tree.body:
        if _in_lead and isinstance(_node, (_ast.Import, _ast.ImportFrom)):
            _lead.append(_node)
        else:
            _in_lead = False
            _rest.append(_node)
    _g = {"__name__": "__main__", "__file__": "__PROBE_PATH__", "__builtins__": builtins}
    exec(compile(_ast.Module(body=_lead, type_ignores=[]), "__PROBE_PATH__", "exec"), _g)
    _stage = "target_execution"          # imports done: the target is reachable
    exec(compile(_ast.Module(body=_rest, type_ignores=[]), "__PROBE_PATH__", "exec"), _g)
except SystemExit as _e:
    if _e.code not in (0, None):
        _status, _tb, _exc = "exception", "SystemExit: %r" % (_e.code,), "SystemExit"
except BaseException as _e:
    _status, _tb, _exc = "exception", traceback.format_exc(), type(_e).__name__

# The runner keeps only a stream's first and last 100,000 characters, so a
# payload line longer than that is cut in the middle and parses as nothing —
# every observation lost. A probe that records a whole grid (180 cases, each an
# array printed as text) gets there easily. The payload is therefore shrunk
# until it fits: sequences shortened, long strings cut, each cut marked, and
# identically on both revisions, which keeps them comparable.
_PAYLOAD_BUDGET = 80000


def _shrink(v, items, chars):
    if isinstance(v, dict):
        return {k: _shrink(x, items, chars) for k, x in v.items()}
    if isinstance(v, list):
        head = [_shrink(x, items, chars) for x in v[:items]]
        return head + (["... %d more" % (len(v) - items)] if len(v) > items else [])
    if isinstance(v, str) and len(v) > chars:
        return v[:chars] + "..."
    return v


_payload = {
    "status": _status,
    "failure_stage": ("" if _status == "ok" else _stage),
    "target_reached": (_stage == "target_execution"),
    "exception_type": _exc,
    "traceback": _tb[-3000:],
    "records": _plain(_RECORDS),
    "iterations": _plain(_ITERATIONS[:500]),
    "warnings": [str(w)[:300] for w in _WARNINGS[:100]],
}
_text = json.dumps(_payload)
_full_records, _full_iterations = _payload["records"], _payload["iterations"]
for _items, _chars in ((200, 400), (100, 200), (50, 120), (20, 80), (8, 40), (3, 20)):
    if len(_text) <= _PAYLOAD_BUDGET:
        break
    _payload["records"] = _shrink(_full_records, _items, _chars)
    _payload["iterations"] = _shrink(_full_iterations, _items, _chars)
    _payload["warnings"] = _payload["warnings"][:20]
    _payload["records_shrunk"] = {"items": _items, "chars": _chars}
    _text = json.dumps(_payload)

sys.stdout.flush()
print("__MARKER__")
print(_text)
'''


# --------------------------------------------------------------------------- #
# 2. JavaScript / TypeScript — globals + dynamic import (works for CJS and ESM)
# --------------------------------------------------------------------------- #
_NODE_HARNESS = r'''/* CommonJS on purpose: `require` works on every Node, and an ESM probe (or an
 * ESM package) falls back to dynamic import — built with new Function so old
 * parsers never see the import() syntax. */
const { pathToFileURL } = require('url');
const dynamicImport = new Function('p', 'return import(p);');

const RECORDS = {}, ITERATIONS = [], WARNINGS = [];
/* Which stage the run reached. Module-scoped because flush() reports it and the
 * runner sets it — a numerical pathology counts only after target_execution. */
let STAGE = 'module_loading';
const G = typeof globalThis !== 'undefined' ? globalThis : global;

/* A name recorded repeatedly (a loop over cases) keeps EVERY value, in order —
 * keeping only the last can keep the one case the bug never touches. */
const REPEATED = '__numagent_repeated__';
G.probe = function probe(name, value) {
  const key = String(name);
  const prior = RECORDS[key];
  if (!(key in RECORDS)) RECORDS[key] = value;
  else if (prior && typeof prior === 'object' && Array.isArray(prior[REPEATED])) prior[REPEATED].push(value);
  else RECORDS[key] = { [REPEATED]: [prior, value] };
  return value;
};
G.probe_iteration = function probe_iteration(fields) {
  ITERATIONS.push(fields || {});
};
G.probeIteration = G.probe_iteration;

process.on('warning', function (w) { WARNINGS.push(w.name + ': ' + w.message); });

function plain(v, depth) {
  depth = depth || 0;
  if (depth > 5) return String(v).slice(0, 200);
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') {
    if (Number.isFinite(v)) return v;
    return Number.isNaN(v) ? 'nan' : (v > 0 ? 'inf' : '-inf');
  }
  if (typeof v === 'bigint') return Number(v);
  if (typeof v === 'boolean' || typeof v === 'string') return v;
  if (ArrayBuffer.isView(v)) return Array.from(v).map((x) => plain(x, depth + 1));
  if (Array.isArray(v)) return v.slice(0, 500).map((x) => plain(x, depth + 1));
  if (typeof v === 'object') {
    // a tensor-ish object that can hand back plain data
    const methods = ['arraySync', 'toArray', 'valueOf'];
    for (let i = 0; i < methods.length; i++) {
      const m = methods[i];
      if (typeof v[m] === 'function') {
        try {
          const out = v[m]();
          if (out !== v) return plain(out, depth + 1);
        } catch (e) { /* fall through to the plain-object path */ }
      }
    }
    const out = {};
    const keys = Object.keys(v).slice(0, 200);
    for (let i = 0; i < keys.length; i++) out[keys[i]] = plain(v[keys[i]], depth + 1);
    return out;
  }
  return String(v).slice(0, 200);
}

function isEsm(err) {
  const text = String((err && (err.code + ' ' + err.message)) || err);
  return /ERR_REQUIRE_ESM|Cannot use import statement outside a module|Unexpected token .?export/
    .test(text);
}

/* The runner keeps only a stream's first and last 100,000 characters: a longer
 * payload line is cut in the middle and parses as nothing. It is shrunk until
 * it fits — sequences shortened, strings cut, each cut marked. */
const PAYLOAD_BUDGET = 80000;
function shrink(v, items, chars) {
  if (Array.isArray(v)) {
    const head = v.slice(0, items).map(function (x) { return shrink(x, items, chars); });
    return v.length > items ? head.concat(['... ' + (v.length - items) + ' more']) : head;
  }
  if (v && typeof v === 'object') {
    const out = {};
    Object.keys(v).forEach(function (k) { out[k] = shrink(v[k], items, chars); });
    return out;
  }
  if (typeof v === 'string' && v.length > chars) return v.slice(0, chars) + '...';
  return v;
}
function flush(status, exc, tb) {
  const payload = {
    status: status,
    failure_stage: (status === 'ok' ? '' : STAGE),
    target_reached: (STAGE === 'target_execution'),
    exception_type: exc,
    traceback: String(tb).slice(-3000),
    records: plain(RECORDS),
    iterations: plain(ITERATIONS.slice(0, 500)),
    warnings: WARNINGS.slice(0, 100).map(function (w) { return String(w).slice(0, 300); }),
  };
  const fullRecords = payload.records, fullIterations = payload.iterations;
  let text = JSON.stringify(payload);
  const steps = [[200, 400], [100, 200], [50, 120], [20, 80], [8, 40], [3, 20]];
  for (let i = 0; i < steps.length && text.length > PAYLOAD_BUDGET; i++) {
    payload.records = shrink(fullRecords, steps[i][0], steps[i][1]);
    payload.iterations = shrink(fullIterations, steps[i][0], steps[i][1]);
    payload.warnings = payload.warnings.slice(0, 20);
    payload.records_shrunk = { items: steps[i][0], chars: steps[i][1] };
    text = JSON.stringify(payload);
  }
  console.log('__MARKER__');
  console.log(text);
}

function loadFailed(err) {
  /* Node distinguishes resolving/parsing a module from evaluating it, and only
   * the second means the probe reached the code under test. */
  const code = String((err && err.code) || '');
  const text = String((err && err.stack) || err);
  return /^(ERR_MODULE_NOT_FOUND|MODULE_NOT_FOUND|ERR_REQUIRE_ESM|ERR_UNKNOWN_FILE_EXTENSION|ERR_UNSUPPORTED_DIR_IMPORT)$/.test(code)
    || err instanceof SyntaxError
    || /Cannot find module|Cannot find package|Cannot use import statement outside a module/.test(text)
    /* thrown while EVALUATING the module, but it is still the probe failing to
     * load its dependencies: an ESM file wrote require(). Node reaches
     * evaluation, so only the message distinguishes it. */
    || /require is not defined in ES module scope|exports is not defined in ES module scope/.test(text);
}

(async function () {
  let status = 'ok', exc = '', tb = '';
  try {
    try {
      require('__PROBE_PATH__');          // CJS probe: the common case
    } catch (e) {
      if (!isEsm(e)) throw e;
      await dynamicImport(pathToFileURL('__PROBE_PATH__').href);   // ESM probe
    }
    STAGE = 'target_execution';
  } catch (e) {
    status = 'exception';
    exc = (e && e.name) || (e && e.constructor && e.constructor.name) || 'Error';
    tb = String((e && e.stack) || e);
    // a probe that could not even be loaded is a broken PROBE, not a defect
    if (loadFailed(e)) {
      exc = 'ModuleNotFoundError';
    } else {
      STAGE = 'target_execution';    // the module ran; this is the target's error
    }
  }
  flush(status, exc, tb);
})();
'''

# the repo's own tsx / ts-node when it has one, else Node's type stripping; a
# host with none of them is reported as a missing toolchain, not a probe failure
_TS_RUNNERS = (
    "if [ -x node_modules/.bin/tsx ]; then node_modules/.bin/tsx __RUNNER__; "
    "elif command -v tsx >/dev/null 2>&1; then tsx __RUNNER__; "
    "elif [ -x node_modules/.bin/ts-node ]; then node_modules/.bin/ts-node __RUNNER__; "
    "elif node --experimental-strip-types -e '' >/dev/null 2>&1; then "
    "node --experimental-strip-types __RUNNER__; "
    "elif node -e '' --experimental-transform-types >/dev/null 2>&1; then "
    "node --experimental-transform-types __RUNNER__; "
    "else echo '__NO_TOOLCHAIN__: tsx|ts-node|node>=22.6 (typescript)'; exit 127; fi"
)


# --------------------------------------------------------------------------- #
# 3. Go — a package inside the module, so repo imports resolve
# --------------------------------------------------------------------------- #
_GO_HARNESS = r'''package main

// Harness for numagent's numerical probe. The probe body lives in body.go in
// this same package: `func Body()`. Record with Probe / ProbeIteration.

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"runtime/debug"
)

var (
	records    = map[string]interface{}{}
	iterations = []map[string]interface{}{}
	warnings   = []string{}
)

// Probe records one named observation (scalar, slice, array, or map).
func Probe(name string, value interface{}) interface{} {
	records[name] = plain(reflect.ValueOf(value), 0)
	return value
}

// ProbeIteration records one solver/loop iteration, e.g.
// ProbeIteration(map[string]interface{}{"residual": r}).
func ProbeIteration(fields map[string]interface{}) {
	row := map[string]interface{}{}
	for k, v := range fields {
		row[k] = plain(reflect.ValueOf(v), 0)
	}
	iterations = append(iterations, row)
}

// ProbeWarning records a numerical warning the code would otherwise swallow.
func ProbeWarning(msg string) { warnings = append(warnings, msg) }

func plainFloat(f float64) interface{} {
	if math.IsNaN(f) {
		return "nan"
	}
	if math.IsInf(f, 1) {
		return "inf"
	}
	if math.IsInf(f, -1) {
		return "-inf"
	}
	return f
}

func plain(v reflect.Value, depth int) interface{} {
	if depth > 5 || !v.IsValid() {
		return fmt.Sprintf("%v", v)
	}
	switch v.Kind() {
	case reflect.Interface, reflect.Ptr:
		if v.IsNil() {
			return nil
		}
		return plain(v.Elem(), depth+1)
	case reflect.Bool:
		return v.Bool()
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
		return v.Int()
	case reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return v.Uint()
	case reflect.Float32, reflect.Float64:
		return plainFloat(v.Float())
	case reflect.Complex64, reflect.Complex128:
		c := v.Complex()
		return map[string]interface{}{"real": plainFloat(real(c)), "imag": plainFloat(imag(c))}
	case reflect.String:
		s := v.String()
		if len(s) > 200 {
			s = s[:200]
		}
		return s
	case reflect.Slice, reflect.Array:
		n := v.Len()
		if n > 500 {
			n = 500
		}
		out := make([]interface{}, 0, n)
		for i := 0; i < n; i++ {
			out = append(out, plain(v.Index(i), depth+1))
		}
		return out
	case reflect.Map:
		out := map[string]interface{}{}
		for _, k := range v.MapKeys() {
			out[fmt.Sprintf("%v", k.Interface())] = plain(v.MapIndex(k), depth+1)
		}
		return out
	}
	// anything else (a struct, a tensor type): its own formatting, kept as text
	s := fmt.Sprintf("%v", v.Interface())
	if len(s) > 200 {
		s = s[:200]
	}
	return s
}

func main() {
	status, exc, tb := "ok", "", ""
	func() {
		defer func() {
			if r := recover(); r != nil {
				status, exc = "exception", "panic"
				tb = fmt.Sprintf("panic: %v\n%s", r, debug.Stack())
			}
		}()
		Body()
	}()
	if len(tb) > 3000 {
		tb = tb[len(tb)-3000:]
	}
	// This harness only runs if the package compiled and linked, so reaching
	// here means the target was reached; a build failure never emits a payload
	// and is staged as module_loading by the caller.
	failureStage := ""
	if status != "ok" {
		failureStage = "target_execution"
	}
	payload, _ := json.Marshal(map[string]interface{}{
		"status":         status,
		"failure_stage":  failureStage,
		"target_reached": true,
		"exception_type": exc,
		"traceback":      tb,
		"records":        records,
		"iterations":     iterations,
		"warnings":       warnings,
	})
	fmt.Println("__MARKER__")
	fmt.Println(string(payload))
}
'''


# --------------------------------------------------------------------------- #
# 4. Rust — an example in the crate, so `use <crate>::…` resolves
# --------------------------------------------------------------------------- #
_RUST_HARNESS = r'''// numagent numerical probe (built as an example of this crate).
// The probe body is `fn body()` below; record with probe_scalar / probe_values /
// probe_text / probe_iteration. No external crates are used.
#![allow(dead_code, unused_imports, unused_variables, non_snake_case)]

use std::sync::Mutex;

static NUMAGENT_RECORDS: Mutex<Vec<String>> = Mutex::new(Vec::new());
static NUMAGENT_ITERS: Mutex<Vec<String>> = Mutex::new(Vec::new());
static NUMAGENT_WARNINGS: Mutex<Vec<String>> = Mutex::new(Vec::new());
static NUMAGENT_PANIC: Mutex<String> = Mutex::new(String::new());

fn numagent_json_str(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars().take(400) {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push(' '),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn numagent_num(v: f64) -> String {
    if v.is_nan() {
        "\"nan\"".to_string()
    } else if v.is_infinite() {
        if v > 0.0 { "\"inf\"".to_string() } else { "\"-inf\"".to_string() }
    } else {
        format!("{:?}", v)
    }
}

/// Record one scalar observation.
pub fn probe_scalar(name: &str, value: f64) {
    NUMAGENT_RECORDS.lock().unwrap()
        .push(format!("{}:{}", numagent_json_str(name), numagent_num(value)));
}

/// Record a sequence of observations under one name.
pub fn probe_values(name: &str, values: &[f64]) {
    let body: Vec<String> = values.iter().take(500).map(|v| numagent_num(*v)).collect();
    NUMAGENT_RECORDS.lock().unwrap()
        .push(format!("{}:[{}]", numagent_json_str(name), body.join(",")));
}

/// Record a non-numeric observation (a Debug/Display rendering).
pub fn probe_text(name: &str, value: &str) {
    NUMAGENT_RECORDS.lock().unwrap()
        .push(format!("{}:{}", numagent_json_str(name), numagent_json_str(value)));
}

/// Record one solver/loop iteration, e.g. probe_iteration(&[("residual", r)]).
pub fn probe_iteration(fields: &[(&str, f64)]) {
    let body: Vec<String> = fields.iter()
        .map(|(k, v)| format!("{}:{}", numagent_json_str(k), numagent_num(*v)))
        .collect();
    NUMAGENT_ITERS.lock().unwrap().push(format!("{{{}}}", body.join(",")));
}

/// Record a numerical warning the code would otherwise swallow.
pub fn probe_warning(msg: &str) {
    NUMAGENT_WARNINGS.lock().unwrap().push(msg.to_string());
}

fn numagent_flush(status: &str, exc: &str, tb: &str) {
    let records = NUMAGENT_RECORDS.lock().unwrap().join(",");
    let iters = NUMAGENT_ITERS.lock().unwrap().join(",");
    let warns: Vec<String> = NUMAGENT_WARNINGS.lock().unwrap().iter()
        .map(|w| numagent_json_str(w)).collect();
    println!("__MARKER__");
    println!(
        // the example built and linked, so the target was reached; a compile
        // failure never reaches this point
        "{{\"status\":{},\"failure_stage\":{},\"target_reached\":true,\"exception_type\":{},\"traceback\":{},\"records\":{{{}}},\"iterations\":[{}],\"warnings\":[{}]}}",
        numagent_json_str(status),
        numagent_json_str(if status == "ok" {{ "" }} else {{ "target_execution" }}),
        numagent_json_str(exc), numagent_json_str(tb),
        records, iters, warns.join(",")
    );
}

__BODY__

fn main() {
    std::panic::set_hook(Box::new(|info| {
        *NUMAGENT_PANIC.lock().unwrap() = format!("{}", info);
    }));
    let outcome = std::panic::catch_unwind(|| body());
    let _ = std::panic::take_hook();
    match outcome {
        Ok(_) => numagent_flush("ok", "", ""),
        Err(_) => {
            let tb = NUMAGENT_PANIC.lock().unwrap().clone();
            numagent_flush("exception", "panic", &tb);
        }
    }
}
'''


# --------------------------------------------------------------------------- #
# 5. Java — compiled against the revision's built classes
# --------------------------------------------------------------------------- #
_JAVA_HARNESS = r'''// numagent numerical probe harness. The probe body is Body.body().
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class NumagentProbe {
    static final Map<String, String> RECORDS = new LinkedHashMap<>();
    static final List<String> ITERATIONS = new ArrayList<>();
    static final List<String> WARNINGS = new ArrayList<>();

    /** Record one scalar observation. */
    public static void probe(String name, double value) {
        RECORDS.put(name, num(value));
    }

    /** Record a sequence of observations under one name. */
    public static void probe(String name, double[] values) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < values.length && i < 500; i++) {
            if (i > 0) sb.append(",");
            sb.append(num(values[i]));
        }
        RECORDS.put(name, sb.append("]").toString());
    }

    /** Record a non-numeric observation (its toString). */
    public static void probe(String name, Object value) {
        if (value instanceof Number) {
            RECORDS.put(name, num(((Number) value).doubleValue()));
        } else {
            RECORDS.put(name, str(String.valueOf(value)));
        }
    }

    /** Record one solver/loop iteration: probeIteration("residual", r). */
    public static void probeIteration(Object... keyValuePairs) {
        StringBuilder sb = new StringBuilder("{");
        for (int i = 0; i + 1 < keyValuePairs.length; i += 2) {
            if (i > 0) sb.append(",");
            sb.append(str(String.valueOf(keyValuePairs[i]))).append(":");
            Object v = keyValuePairs[i + 1];
            sb.append(v instanceof Number ? num(((Number) v).doubleValue())
                                          : str(String.valueOf(v)));
        }
        ITERATIONS.add(sb.append("}").toString());
    }

    /** Record a numerical warning the code would otherwise swallow. */
    public static void probeWarning(String message) { WARNINGS.add(str(message)); }

    static String num(double v) {
        if (Double.isNaN(v)) return "\"nan\"";
        if (Double.isInfinite(v)) return v > 0 ? "\"inf\"" : "\"-inf\"";
        return Double.toString(v);
    }

    static String str(String s) {
        if (s == null) return "\"null\"";
        if (s.length() > 400) s = s.substring(0, 400);
        StringBuilder sb = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default: sb.append(c < 0x20 ? ' ' : c);
            }
        }
        return sb.append("\"").toString();
    }

    static String join(Iterable<String> parts) {
        StringBuilder sb = new StringBuilder();
        for (String p : parts) {
            if (sb.length() > 0) sb.append(",");
            sb.append(p);
        }
        return sb.toString();
    }

    public static void main(String[] args) {
        String status = "ok", exc = "", tb = "", stage = "";
        try {
            Body.body();
        } catch (NoClassDefFoundError | UnsatisfiedLinkError t) {
            // the classes compiled but something they need is not on the
            // classpath at run time: the target was never reached
            status = "exception";
            stage = "module_loading";
            exc = t.getClass().getSimpleName();
            tb = String.valueOf(t);
        } catch (Throwable t) {
            status = "exception";
            stage = "target_execution";
            exc = t.getClass().getSimpleName();
            StringBuilder sb = new StringBuilder(String.valueOf(t));
            for (StackTraceElement e : t.getStackTrace()) sb.append("\n\tat ").append(e);
            tb = sb.length() > 3000 ? sb.substring(sb.length() - 3000) : sb.toString();
        }
        List<String> recs = new ArrayList<>();
        for (Map.Entry<String, String> e : RECORDS.entrySet()) {
            recs.add(str(e.getKey()) + ":" + e.getValue());
        }
        System.out.println("__MARKER__");
        System.out.println("{\"status\":" + str(status)
            + ",\"failure_stage\":" + str(stage)
            + ",\"target_reached\":" + (stage.equals("module_loading") ? "false" : "true")
            + ",\"exception_type\":" + str(exc)
            + ",\"traceback\":" + str(tb)
            + ",\"records\":{" + join(recs) + "}"
            + ",\"iterations\":[" + join(ITERATIONS) + "]"
            + ",\"warnings\":[" + join(WARNINGS) + "]}");
    }
}
'''

#: built from the revision's own layout, then whatever the agent adds
_JAVA_CLASSPATH = (
    'CP="target/classes:target/test-classes:build/classes/java/main:'
    'build/classes/java/test:build/classes/kotlin/main"; '
    '[ -f .numagent_cp ] && CP="$CP:$(cat .numagent_cp)"; '
    'for j in target/dependency/*.jar lib/*.jar target/*.jar; do '
    '[ -f "$j" ] && CP="$CP:$j"; done'
)


# --------------------------------------------------------------------------- #
# 6. C / C++ — one header; a destructor flushes, so `main` needs no edits
# --------------------------------------------------------------------------- #
_C_HEADER = r'''/* numagent numerical probe API (C and C++).
 * probe_scalar / probe_array / probe_text record observations, probe_iter
 * records one solver iteration. The payload is flushed automatically when the
 * program exits, and IEEE FP exception flags raised along the way are reported.
 */
#ifndef NUMAGENT_PROBE_H
#define NUMAGENT_PROBE_H

#include <fenv.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NUMAGENT_BUF 262144
static char numagent_records[NUMAGENT_BUF];
static char numagent_iters[NUMAGENT_BUF];
static char numagent_warns[8192];
static int numagent_nrec = 0, numagent_niter = 0;

static void numagent_num(char *out, size_t n, double v) {
    if (isnan(v)) snprintf(out, n, "\"nan\"");
    else if (isinf(v)) snprintf(out, n, v > 0 ? "\"inf\"" : "\"-inf\"");
    else snprintf(out, n, "%.17g", v);
}

static void numagent_append(char *buf, const char *text) {
    size_t used = strlen(buf);
    if (used + strlen(text) + 2 < NUMAGENT_BUF) strcat(buf, text);
}

/* Record one scalar observation. */
static void probe_scalar(const char *name, double value) {
    char num[64], row[512];
    numagent_num(num, sizeof(num), value);
    snprintf(row, sizeof(row), "%s\"%s\":%s", numagent_nrec++ ? "," : "", name, num);
    numagent_append(numagent_records, row);
}

/* Record `n` values under one name. */
static void probe_array(const char *name, const double *values, int n) {
    char row[256], num[64];
    snprintf(row, sizeof(row), "%s\"%s\":[", numagent_nrec++ ? "," : "", name);
    numagent_append(numagent_records, row);
    if (n > 500) n = 500;
    for (int i = 0; i < n; i++) {
        numagent_num(num, sizeof(num), values[i]);
        numagent_append(numagent_records, i ? "," : "");
        numagent_append(numagent_records, num);
    }
    numagent_append(numagent_records, "]");
}

/* Record a non-numeric observation. */
static void probe_text(const char *name, const char *value) {
    char row[768];
    snprintf(row, sizeof(row), "%s\"%s\":\"%.400s\"",
             numagent_nrec++ ? "," : "", name, value);
    numagent_append(numagent_records, row);
}

/* Record one solver/loop iteration, e.g. probe_iter("residual", r). */
static void probe_iter(const char *field, double value) {
    char num[64], row[512];
    numagent_num(num, sizeof(num), value);
    snprintf(row, sizeof(row), "%s{\"%s\":%s}", numagent_niter++ ? "," : "", field, num);
    numagent_append(numagent_iters, row);
}

/* Record a numerical warning the code would otherwise swallow. */
static void probe_warning(const char *message) {
    char row[512];
    snprintf(row, sizeof(row), "%s\"%.400s\"", strlen(numagent_warns) ? "," : "", message);
    numagent_append(numagent_warns, row);
}

static void numagent_fp_flags(void) {
    /* phrased the way the oracle reads FP exceptions */
    if (fetestexcept(FE_INVALID)) probe_warning("FE_INVALID: invalid value encountered");
    if (fetestexcept(FE_DIVBYZERO)) probe_warning("FE_DIVBYZERO: divide by zero encountered");
    if (fetestexcept(FE_OVERFLOW)) probe_warning("FE_OVERFLOW: overflow encountered");
    if (fetestexcept(FE_UNDERFLOW)) probe_warning("FE_UNDERFLOW: underflow encountered");
}

__attribute__((constructor)) static void numagent_probe_init(void) {
    feclearexcept(FE_ALL_EXCEPT);
}

/* The flush runs after main(), which means the program compiled, linked and
 * started: the target was reached. A compile or link failure produces no
 * payload at all and is staged as module_loading by the caller. */
__attribute__((destructor)) static void numagent_probe_flush(void) {
    numagent_fp_flags();
    fflush(stdout);
    printf("__MARKER__\n");
    printf("{\"status\":\"ok\",\"failure_stage\":\"\",\"target_reached\":true,"
           "\"exception_type\":\"\",\"traceback\":\"\","
           "\"records\":{%s},\"iterations\":[%s],\"warnings\":[%s]}\n",
           numagent_records, numagent_iters, numagent_warns);
    fflush(stdout);
}

#ifdef __cplusplus
}  /* extern "C" */

#include <vector>
static inline void probe(const char *name, double value) { probe_scalar(name, value); }
static inline void probe(const char *name, const std::vector<double> &v) {
    probe_array(name, v.data(), (int)v.size());
}
#endif

#endif /* NUMAGENT_PROBE_H */
'''


# --------------------------------------------------------------------------- #
# plan builder
# --------------------------------------------------------------------------- #
#: per-language documentation of what `code` must contain — surfaced in the tool
#: schema so the agent writes the right shape the first time
CODE_CONTRACT = {
    "python": "a complete Python script. Builtins injected: probe(name, value) and "
              "probe_iteration(**fields). The revision's directory is the cwd and on "
              "sys.path, so an import resolves to THIS revision.",
    "javascript": "a complete JavaScript module (ESM or CJS, top-level await allowed), "
                  "run from the revision's root so require/import of the repo and its "
                  "node_modules resolve. Globals injected: probe(name, value) and "
                  "probeIteration({residual: r}).",
    "typescript": "a complete TypeScript module, run from the revision's root via the "
                  "repo's tsx/ts-node (or Node's type stripping). Globals injected: "
                  "probe(name, value) and probeIteration({residual: r}).",
    "go": "a Go file in `package main` that defines `func Body()` — NOT func main. "
          "Put your imports in a normal import block; the repo's module path is "
          "importable because the probe is compiled as a package inside it. Record "
          "with Probe(name, value) and ProbeIteration(map[string]interface{}{...}).",
    "rust": "Rust items that define `fn body()` — NOT fn main. `use` lines are fine; "
            "the probe is built as an example of the crate, so `use <crate_name>::…` "
            "resolves to THIS revision's public API. Record with probe_scalar(name, v), "
            "probe_values(name, &[..]), probe_text(name, s), "
            "probe_iteration(&[(\"residual\", r)]).",
    "java": "a Java class `Body` with `public static void body() throws Exception` — "
            "imports above the class are fine. Compiled against the revision's built "
            "classes (build them first, e.g. `mvn -q -DskipTests compile`; for a "
            "dependency classpath run `mvn dependency:build-classpath "
            "-Dmdep.outputFile=.numagent_cp`). Record with NumagentProbe.probe(name, "
            "value) and NumagentProbe.probeIteration(\"residual\", r).",
    "c": "a complete C program with `int main(void)`. numagent_probe.h is included "
         "for you: probe_scalar(name, v), probe_array(name, ptr, n), probe_text(name, s), "
         "probe_iter(\"residual\", r). Compiled with -I the revision root; pass extra "
         "flags (-I/-L/-l) via extra_flags. IEEE FP flags are reported automatically.",
    "cpp": "a complete C++ program with `int main()`. numagent_probe.h is included for "
           "you: probe(name, v) / probe(name, std::vector<double>), probe_array, "
           "probe_text, probe_iter(\"residual\", r). Compiled with -I the revision root; "
           "pass extra flags via extra_flags. IEEE FP flags are reported automatically.",
}


#: `docker exec bash -lc` runs a login shell, and /etc/profile resets PATH — so a
#: toolchain the image installed outside /usr/bin (golang, rustup, a JDK) is not on
#: it. Put the usual install dirs back before anything is looked up.
_PATH_PRELUDE = (
    'export PATH="$PATH:/usr/local/go/bin:/usr/lib/go/bin:/usr/local/cargo/bin:'
    '$HOME/.cargo/bin:$HOME/go/bin${JAVA_HOME:+:$JAVA_HOME/bin}"; '
)


#: compiler/build diagnostics — a probe that never built is a broken PROBE, the
#: compiled-language counterpart of Python's ModuleNotFoundError
_BUILD_FAILURE_MARKERS = (
    "error:", "error[e", "cannot find symbol", "undefined reference",
    "undefined:", "undeclared", "syntax error", "no required module provides",
    "cannot find package", "could not compile", "compilation terminated",
    "compilation failed", "does not exist", "cannot find module",
    "implicit declaration of function",
)


def looks_like_build_failure(output: str) -> bool:
    """True when `output` reads as a compiler/build diagnostic."""
    low = (output or "").lower()
    return any(m in low for m in _BUILD_FAILURE_MARKERS)


def _toolchain_guard(language: str) -> str:
    tool = TOOLCHAIN[language]
    return (_PATH_PRELUDE + f"command -v {tool} >/dev/null 2>&1 || "
            f"{{ echo '{NO_TOOLCHAIN}: {tool} ({language})'; exit 127; }}; ")


def plan_probe(language: str, code: str, *, marker: str, workdir: str,
               extra_flags: str = "", package=None) -> ProbePlan:
    """Files + command that run `code` as a probe in `language`.

    `package` is ``(name, directory)`` for languages where a probe must be built
    inside a specific package — a Rust crate in a workspace, where the repository
    root owns no package at all.

    Raises ValueError for a language with no harness.
    """
    lang = canonical(language)
    if lang not in CODE_CONTRACT:
        raise ValueError(f"no probe harness for language {language!r} "
                         f"(have: {', '.join(sorted(CODE_CONTRACT))})")
    guard = _toolchain_guard(lang)
    render = lambda text, **kw: text.replace("__MARKER__", marker)  # noqa: E731

    if lang == "python":
        probe_path, runner = "/tmp/_numagent_probe.py", "/tmp/_numagent_runner.py"
        return ProbePlan(
            language=lang,
            files={probe_path: code,
                   runner: render(_PYTHON_HARNESS).replace("__PROBE_PATH__", probe_path)},
            # -u so a probe's own prints reach a live output stream as they
            # happen, instead of arriving in one block when the process exits
            command=f"{guard}PYTHONWARNINGS=always python -u {runner}",
        )

    if lang in ("javascript", "typescript"):
        # both files live in the revision so relative imports / node_modules resolve
        ext = "ts" if lang == "typescript" else "js"
        probe_path = f"{workdir}/_numagent_probe.{ext}"
        runner = f"{workdir}/_numagent_runner.cjs"   # .cjs stays CJS in an ESM package
        files = {probe_path: code,
                 runner: render(_NODE_HARNESS).replace("__PROBE_PATH__", probe_path)}
        cmd = (f"node {runner}" if lang == "javascript"
               else _TS_RUNNERS.replace("__RUNNER__", runner)
                               .replace("__NO_TOOLCHAIN__", NO_TOOLCHAIN))
        return ProbePlan(language=lang, files=files, command=guard + cmd)

    if lang == "go":
        pkg = f"{workdir}/_numagent_probe"
        return ProbePlan(
            language=lang, dirs=(pkg,),
            files={f"{pkg}/harness.go": render(_GO_HARNESS), f"{pkg}/body.go": code},
            command=f"{guard}go run ./_numagent_probe {extra_flags}".strip(),
        )

    if lang == "rust":
        # A cargo WORKSPACE root is not a package: `cargo run --example` there
        # has nothing to attach the example to and fails before the probe runs.
        # Building it inside the crate under test also makes `use <crate>::…`
        # resolve, which is what a probe needs anyway.
        name, directory = (package if isinstance(package, tuple) else (package or "", ""))
        base = f"{workdir}/{directory}".rstrip("/") if directory else workdir
        example = f"{base}/examples/numagent_probe.rs"
        select = f"-p {name} " if name else ""
        return ProbePlan(
            language=lang, dirs=(f"{base}/examples",),
            files={example: render(_RUST_HARNESS).replace("__BODY__", code)},
            command=(f"{guard}cargo run --quiet {select}--example numagent_probe "
                     f"{extra_flags}").strip(),
        )

    if lang == "java":
        src = f"{workdir}/_numagent_probe"
        classes = "/tmp/_numagent_classes"
        cp_extra = f":{extra_flags}" if extra_flags else ""
        return ProbePlan(
            language=lang, dirs=(src,),
            files={f"{src}/NumagentProbe.java": render(_JAVA_HARNESS),
                   f"{src}/Body.java": code},
            command=(f"{guard}{_JAVA_CLASSPATH}; mkdir -p {classes} && "
                     f'javac -nowarn -cp "$CP{cp_extra}" -d {classes} {src}/*.java && '
                     f'java -cp "$CP{cp_extra}:{classes}" NumagentProbe'),
        )

    # C / C++
    header = "/tmp/numagent_probe.h"
    ext, compiler, std = (("c", "cc", "-std=c11") if lang == "c"
                          else ("cpp", "g++", "-std=c++17"))
    src, binary = f"/tmp/_numagent_probe.{ext}", "/tmp/_numagent_probe_bin"
    return ProbePlan(
        language=lang,
        files={header: render(_C_HEADER),
               src: f'#include "numagent_probe.h"\n{code}\n'},
        command=(f"{guard}{compiler} {std} -I/tmp -I{workdir} -I{workdir}/include "
                 f"{src} -o {binary} {extra_flags} -lm && {binary}"),
    )


# --------------------------------------------------------------------------- #
# the reported-example probe (harness-generated, not model-authored)
# --------------------------------------------------------------------------- #
#: Languages whose probe can evaluate an expression given as text. The mined
#: examples ARE expressions, so only these can be checked without asking a model
#: to translate them — and a translation is exactly what must not be trusted here.
EXAMPLE_LANGUAGES = ("python", "javascript", "typescript")

#: Prefix marking a value that could not be evaluated. The comparator reads it as
#: "not observed" rather than as a wrong value, so a broken preamble never looks
#: like a fix that failed.
EXAMPLE_ERROR = "__error__: "


def is_example_error(value) -> bool:
    """Is this recorded value an evaluation failure rather than an observation?

    Matched WITHOUT the trailing space. A generated probe writes this marker
    itself, and a model asked for `"__error__: " + type(e).__name__` writes
    `f"__error__:{type(e).__name__}"` often enough that one missing space cannot
    be allowed to decide anything: on statsmodels#10214 it made three crashed
    examples read as three observed values that happened to agree on both
    revisions (`0/3 not_reproduced`), and it hid them from the repair loop,
    which only rewrites a probe whose examples it can SEE have failed.
    """
    if isinstance(value, dict):
        leaves = list(value.values())
    elif isinstance(value, (list, tuple)):
        leaves = list(value)
    else:
        return isinstance(value, str) and value.lstrip().startswith(EXAMPLE_ERROR.rstrip())
    # A probe records a container when the example sweeps a setting
    # (`{'use_legacy_pandas_api=False': …}`) or repeats a case, and the call can
    # raise in every one of them — astropy#20240, where the buggy revision raised
    # `TypeError: cannot safely cast non-equivalent float64 to int64` under both
    # settings and the row read as two observed values instead. EVERY leaf must be
    # an error: one case failing out of six is still five measurements.
    return bool(leaves) and all(is_example_error(v) for v in leaves)


def example_error_text(value) -> str:
    """The message behind :func:`is_example_error`, marker and spacing removed.

    For a container of errors, the first one: they are the same call failing
    under each case, and the message is what names the failure."""
    if not is_example_error(value):
        return ""
    if isinstance(value, dict):
        value = next(iter(value.values()), "")
    elif isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    if not isinstance(value, str):
        return example_error_text(value)
    return str(value).lstrip()[len(EXAMPLE_ERROR.rstrip()):].lstrip(": ").strip()

#: A reported expected output is often an EXPRESSION in the revision's own
#: language ("frac(-1, 2)", "big(8615841276005)/big(14322)"), not a literal a
#: comparator can parse. Evaluating it in the revision turns the report's claim
#: into a value, recorded under this prefix so it never pairs as an observation.
EXPECTED_PREFIX = "expected::"

_PY_EXAMPLES = """\
{preamble}
# [record name, expression, bindings the report attached, reported expected text]
__numagent_cases__ = {inputs}
for __numagent_name, __numagent_expr, __numagent_binds, __numagent_want in __numagent_cases__:
    try:
        for __numagent_bind in __numagent_binds:
            exec(__numagent_bind)
        probe(__numagent_name, eval(__numagent_expr))
    except Exception as __numagent_err:
        probe(__numagent_name, "{error}" + type(__numagent_err).__name__
              + ": " + str(__numagent_err)[:200])
    if __numagent_want:
        try:    # the expectation may itself be an expression in this language
            probe("{expected}" + __numagent_name, eval(__numagent_want))
        except Exception:
            pass                    # prose, not code — the comparator handles it
"""

_JS_EXAMPLES = """\
{preamble}
const __numagentCases = {inputs};
for (const [__name, __expr, __binds, __want] of __numagentCases) {{
  try {{
    for (const __b of __binds) {{ eval("var " + __b); }}
    probe(__name, eval(__expr));
  }} catch (e) {{
    probe(__name, "{error}" + ((e && e.name) ? e.name : "Error") + ": " +
          String((e && e.message) ? e.message : e).slice(0, 200));
  }}
  if (__want) {{
    try {{ probe("{expected}" + __name, eval(__want)); }} catch (e) {{ }}
  }}
}}
"""


#: Command that reports a JS/TS package's module system, read from package.json
#: rather than inferred from a failure. `"type": "module"` means the probe must
#: use `import`; anything else (or no manifest) means `require` works.
JS_MODULE_STYLE_COMMAND = (
    "node -e \"try{const p=require('./package.json');"
    "process.stdout.write(p.type==='module'?'esm':'commonjs')}"
    "catch(e){process.stdout.write('commonjs')}\" 2>/dev/null || echo commonjs")


def js_module_style(output: str) -> str:
    """Read :data:`JS_MODULE_STYLE_COMMAND`'s output ('esm' or 'commonjs')."""
    return "esm" if "esm" in (output or "").strip().lower() else "commonjs"


def js_import_hint(style: str, module_path: str = "./src/index.js") -> str:
    """The import form this package actually supports, as a line to copy.

    A model asked to write a preamble for an ESM package will reach for
    `require` about half the time, and the failure it gets back
    ("require is not defined in ES module scope") arrives only after a probe run
    has been spent. package.json answers it beforehand, for free.
    """
    if style == "esm":
        return (f"This package is ESM (\"type\": \"module\" in package.json) — use "
                f"`import x from '{module_path}'`. `require(...)` will fail with "
                f"\"require is not defined in ES module scope\".")
    return (f"This package is CommonJS — use `const x = require('{module_path}')`. "
            f"A static `import` will fail with \"Cannot use import statement "
            f"outside a module\".")


def _assigned_values(text: str) -> str:
    """An expected output that ASSIGNS, as the expression for all it assigns.

    `xw = np.nan; yw = 5` names the answer variable by variable — here the two
    outputs of a call returning `(xw, yw)`. Read as "setup; expression" it kept
    only the last value, so a revision returning exactly `(nan, 5)` was scored
    against `5` and a correct fix recorded as still wrong. Every assigned value
    is kept, in the order written: `(np.nan, 5)`. "" for anything else.
    """
    import ast
    source = str(text or "").strip()
    try:
        body = ast.parse(source).body
    except (SyntaxError, ValueError):
        return ""
    if not body or not all(isinstance(node, ast.Assign) and len(node.targets) == 1
                           and isinstance(node.targets[0], ast.Name) for node in body):
        return ""
    values = [ast.get_source_segment(source, node.value) for node in body]
    if any(v is None for v in values):
        return ""
    return values[0] if len(values) == 1 else "(" + ", ".join(values) + ")"


def example_probe_source(language: str, examples: list, preamble: str = "") -> str:
    """Probe source that evaluates each mined example and records it BY ITS INPUT.

    The record name is the example's own input text, so the comparator pairs
    observation to expectation without trusting a model to have labelled sixteen
    records identically on two revisions. `preamble` is the only model-supplied
    part — the imports and bindings the expressions need (for SymPy, that is
    ``from sympy import *`` and ``z = Symbol('z')``).

    Raises ValueError for a language that cannot evaluate an expression string.
    """
    import json as _json

    lang = canonical(language)
    if lang not in EXAMPLE_LANGUAGES:
        raise ValueError(
            f"the reported examples cannot be evaluated in {language!r} (only "
            f"{', '.join(EXAMPLE_LANGUAGES)} can evaluate an expression given as text) "
            f"— record them with run_numerical_probe instead, naming each record "
            f"exactly as the example's input")
    # the record name stays the report's own text; only what RUNS is cleaned
    cases = []
    for entry in examples:
        raw, want = entry if isinstance(entry, (tuple, list)) else (entry, "")
        expression, bindings = split_example_input(raw, lang)
        want_expr = ((_assigned_values(want) if lang == "python" else "")
                     or (split_example_input(want, lang)[0] if want else ""))
        cases.append([str(raw), expression, bindings, want_expr])
    template = _PY_EXAMPLES if lang == "python" else _JS_EXAMPLES
    return template.format(preamble=(preamble or "").strip(),
                           inputs=_json.dumps(cases),
                           error=EXAMPLE_ERROR, expected=EXPECTED_PREFIX)


#: a mined example often reads "expr where c = b**2/12 (delta=0 case)" — prose the
#: report's author added for a human. `where` clauses are BINDINGS, the rest is
#: annotation; both have to come off before the expression can be evaluated.
_WHERE = re.compile(r"\s+where\s+", re.I)
_TRAILING_GROUP = re.compile(r"\s*[\(\[\{][^\(\)\[\]\{\}]*[\)\]\}]\s*$")


def _parses(text: str, mode: str = "eval") -> bool:
    import ast
    try:
        ast.parse(text.strip(), mode=mode)
        return True
    except (SyntaxError, ValueError):
        return False


def _strip_annotation(text: str, mode: str = "eval") -> str:
    """Drop trailing bracketed prose until what remains parses. Nothing is
    removed from a string that already parses, so a real expression is safe."""
    candidate = text.strip()
    for _ in range(4):
        if _parses(candidate, mode):
            return candidate
        stripped = _TRAILING_GROUP.sub("", candidate)
        if stripped == candidate:
            break
        candidate = stripped
    return candidate


def _balanced(text: str) -> bool:
    depth = 0
    for ch in text:
        depth += (ch in "([{") - (ch in ")]}")
        if depth < 0:
            return False
    return depth == 0


#: a trailing " [note]" / " (note)" — prose annotations are separated by a space,
#: where a real index or call in code is not.
_TRAILING_NOTE = re.compile(r"\s+[\(\[][^()\[\]]*[\)\]]\s*$")


def _clean_foreign(text: str) -> str:
    """Trim a trailing annotation from an expression in a NON-Python language.

    Python's parser cannot judge these — ``frac(495057205241079648212477525n,
    66n)`` is valid JavaScript and a syntax error in Python, and treating it as
    Python throws the arguments away and leaves a bare function name. So the only
    rules applied here are bracket balance and the space that separates prose
    from code.
    """
    candidate = text.strip()
    for _ in range(3):
        stripped = _TRAILING_NOTE.sub("", candidate)
        if stripped == candidate or not _balanced(stripped):
            break
        candidate = stripped
    return candidate


def _top_level_pieces(text: str) -> list:
    """Split on `;` and newlines that sit outside every bracket and string.

    A mined example is often a small script rather than one expression —
    ``x = 20 + log(1 + S(10)**-10); x > 20`` binds a name and then asks a
    question about it. Evaluating that whole text as an expression is a syntax
    error, so the statements have to come off the front.
    """
    pieces, cur, depth, quote = [], [], 0, ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch in ";\n" and depth <= 0:
            pieces.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    pieces.append("".join(cur))
    return [piece.strip() for piece in pieces if piece.strip()]


#: `const x = 1` is a binding too, but the JS harness supplies its own `var`.
_DECLARATOR = re.compile(r"^\s*(?:const|let|var)\s+")


#: `a(n) = ...`, `x = ...` — an example's last line is often written as a
#: definition, where what is being reported is the value on the RIGHT.
_COMPARISON = ("==", "!=", "<=", ">=", "=>", "=<")


def _rhs(text: str) -> str:
    """What sits after the first top-level `=` that is an assignment, or ""."""
    depth, quote, i = 0, "", 0
    while i < len(text):
        ch = text[i]
        pair = text[i:i + 2]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif pair in _COMPARISON:
            i += 2
            continue
        elif ch == "=" and depth <= 0:
            return text[i + 1:].strip()
        i += 1
    return ""


def _expression_of_statement(piece: str) -> str:
    """The value a single Python statement is really reporting, or "".

    A report writes its example as a claim — ``assert p * x == px`` — or as a
    definition. Both name one value; that value is what a probe records.
    """
    import ast

    try:
        body = ast.parse(piece.strip(), mode="exec").body
    except (SyntaxError, ValueError):
        return ""
    if len(body) != 1:
        return ""
    node = body[0]
    inner = None
    if isinstance(node, ast.Assert):
        inner = node.test
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        inner = node.value
    elif isinstance(node, ast.Expr):
        inner = node.value
    if inner is None:
        return ""
    try:
        return ast.unparse(inner)
    except AttributeError:          # Python < 3.9
        return ""


def _final_expression(piece: str) -> str:
    """The evaluable expression a final statement is reporting — "" when none.

    ``a(n) = int(2**n * log(phi)/log(10)) + 1`` is mathematical notation, not
    Python: the name on the left is the report's own label for the value on the
    right, and the right is what has to be evaluated.
    """
    if _parses(piece):
        return piece
    if _WHERE.search(piece):        # bindings, handled by the `where` path
        return ""
    statement = _expression_of_statement(piece)
    if statement and _parses(statement):
        return statement
    rhs = _rhs(piece)
    return rhs if rhs and _parses(rhs) else ""


def split_example_input(text: str, language: str = "python"):
    """A mined example input as (expression, bindings).

    Returns the raw text unchanged when it is already a plain expression. The
    record name stays the ORIGINAL text either way — that is what the comparator
    pairs on — so cleaning changes what runs, never what it is called.
    """
    raw = str(text or "").strip()
    if canonical(language) != "python":
        pieces = _top_level_pieces(raw)
        if len(pieces) > 1 and _balanced(pieces[-1]):
            binds = [_DECLARATOR.sub("", piece) for piece in pieces[:-1] if "=" in piece]
            if len(binds) == len(pieces) - 1:
                last = _clean_foreign(pieces[-1])
                return (_rhs(last) or last), binds
        head, tail = raw, ""
        for m in _WHERE.finditer(raw):
            if _balanced(raw[:m.start()]):
                head, tail = raw[:m.start()], raw[m.end():]
                break
        bindings = [b.strip() for b in re.split(r"\s*,\s*", _clean_foreign(tail))
                    if "=" in b]
        return _clean_foreign(head), bindings
    if _parses(raw):
        return raw, []
    # "setup; question" — the statements bind, the last piece is what is asked
    pieces = _top_level_pieces(raw)
    if len(pieces) > 1 and all(_parses(piece, mode="exec") for piece in pieces[:-1]):
        last = _final_expression(pieces[-1])
        if last:
            return last, pieces[:-1]
    # only a `where` OUTSIDE every bracket introduces bindings; one inside is
    # part of the author's prose ("[perfect square case where delta=0]")
    head, tail, depth = raw, "", 0
    for m in _WHERE.finditer(raw):
        depth += (raw[:m.start()].count("(") + raw[:m.start()].count("[")
                  - raw[:m.start()].count(")") - raw[:m.start()].count("]"))
        if depth <= 0:
            head, tail = raw[:m.start()], raw[m.end():]
            break
        depth = 0
    expr = _strip_annotation(head)
    bindings = []
    for piece in re.split(r"\s*,\s*|\s+and\s+", tail or ""):
        binding = _strip_annotation(piece, mode="exec")
        if binding and "=" in binding and _parses(binding, mode="exec"):
            bindings.append(binding)
    # `_strip_annotation` chews on a statement it cannot parse ("assert f(7)"
    # loses its argument), so the unstripped head gets the first try
    fallback = _final_expression(head) or _final_expression(expr)
    return (expr if _parses(expr) else (fallback or raw)), bindings



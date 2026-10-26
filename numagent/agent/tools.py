from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import detect as _detect
from .. import docker_gen, docker_run
from .. import numeric as _numeric
from .. import steps
from ..models import to_dict
from ..shell import run

_MAX_READ = 20_000

# Directories whose files are tests — editing them to force a green run is the
# classic auto-fix failure mode, so we flag (not block) changes here.
_TEST_MARKERS = ("test", "tests", "spec")


class ToolRegistry:
    def __init__(self, repo_path: str, timeout_s: float = 900.0,
                 allow_edit: bool = False, allow_docker: bool = False,
                 allow_numeric: bool = False):
        self.root = Path(repo_path).resolve()
        self.timeout_s = timeout_s
        self.allow_edit = allow_edit
        self.allow_docker = allow_docker
        self.allow_numeric = allow_numeric
        self.image_tag = docker_gen._slug(str(self.root))
        self._det = None                 # cached DetectResult
        self.finished: Optional[Dict[str, str]] = None  # set by report_result
        self.changes: List[Dict[str, str]] = []          # file edits, for the final diff

    # ---- path safety ---------------------------------------------------- #
    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not (p == self.root or self.root in p.parents):
            raise ValueError(f"path {rel!r} escapes the repository root")
        return p

    def _detect_result(self):
        if self._det is None:
            self._det = _detect.detect(str(self.root))
        return self._det

    # ---- schemas (neutral format) --------------------------------------- #
    def schemas(self) -> List[Dict[str, Any]]:
        obj = lambda props, required=(): {   # noqa: E731
            "type": "object", "properties": props,
            "required": list(required), "additionalProperties": False,
        }
        schemas = [
            {"name": "list_dir", "description": "List files and directories at a path relative to the repo root.",
             "parameters": obj({"path": {"type": "string", "description": "Relative path; '.' for the root."}})},
            {"name": "read_file", "description": "Read a UTF-8 text file relative to the repo root (truncated if large).",
             "parameters": obj({"path": {"type": "string"}}, ["path"])},
            {"name": "run_command", "description": "Run a shell command in the repo root and return exit code + output. Use for inspection and ad-hoc build/test steps.",
             "parameters": obj({"command": {"type": "string"}}, ["command"])},
            {"name": "detect_build_system", "description": "Heuristically detect the repo's language, build system, and any missing toolchains.",
             "parameters": obj({})},
            {"name": "build", "description": "Build the repository using the detected build system (venv-isolated for Python). Returns success + command logs.",
             "parameters": obj({})},
            {"name": "test", "description": "Run the repository's test suite using the detected build system. Returns pass/fail counts + logs.",
             "parameters": obj({})},
        ]
        if self.allow_edit:
            schemas += [
                {"name": "edit_file", "description": "Replace an exact, unique string in a file (targeted patch). old_str must match exactly once. Prefer this over write_file for small changes.",
                 "parameters": obj({
                     "path": {"type": "string"},
                     "old_str": {"type": "string", "description": "Exact text to replace; must occur exactly once."},
                     "new_str": {"type": "string", "description": "Replacement text."},
                 }, ["path", "old_str", "new_str"])},
                {"name": "write_file", "description": "Create or overwrite a file with the given content. Use for new files or full rewrites.",
                 "parameters": obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])},
            ]
        if self.allow_docker:
            schemas += [
                {"name": "suggest_docker_draft", "description": "Return a reference Dockerfile + run.sh generated from the detected build system. A starting point you can adapt with write_file — not written to disk automatically.",
                 "parameters": obj({})},
                {"name": "run_in_docker", "description": "Build the image from .numagent/Dockerfile (context = repo root) and run it (executes .numagent/run.sh). Returns the build/run logs and pass/fail. Write the Dockerfile and run.sh first.",
                 "parameters": obj({})},
            ]
        if self.allow_numeric:
            num = {"type": "array", "items": {"type": "number"}}
            schemas += [
                {"name": "check_numeric_close", "description": "Check that computed values match expected values within tolerance (numpy.allclose semantics: |a-e| <= atol + rtol*|e|). Reports the worst offender.",
                 "parameters": obj({
                     "actual": num, "expected": num,
                     "rtol": {"type": "number", "description": "relative tolerance (default 1e-7)"},
                     "atol": {"type": "number", "description": "absolute tolerance (default 0)"},
                 }, ["actual", "expected"])},
                {"name": "check_convergence", "description": "Given grid sizes and the corresponding errors, fit the observed order of convergence (error ~ h^p, h~1/size) and optionally check it against an expected order.",
                 "parameters": obj({
                     "sizes": num, "errors": num,
                     "expected_order": {"type": "number", "description": "e.g. 2 for a 2nd-order method (optional)"},
                 }, ["sizes", "errors"])},
                {"name": "check_reproducibility", "description": "Run a shell command several times in the repo and verify its numeric output is stable across runs (catches unseeded RNG, races, uninitialized memory).",
                 "parameters": obj({
                     "command": {"type": "string"},
                     "runs": {"type": "integer", "description": "number of runs (default 3)"},
                 }, ["command"])},
            ]
        schemas.append(
            {"name": "report_result", "description": "Finish: report the final verdict for this repository. Call exactly once when done.",
             "parameters": obj({
                 "verdict": {"type": "string", "enum": ["pass", "fail"]},
                 "summary": {"type": "string", "description": "One-paragraph summary of what happened (and what you changed, if anything)."},
             }, ["verdict", "summary"])}
        )
        return schemas

    # ---- dispatch ------------------------------------------------------- #
    def call(self, name: str, args: Dict[str, Any]) -> str:
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return f"[error] unknown tool: {name}"
            return handler(args)
        except Exception as e:  # tools never raise into the loop
            return f"[error] {type(e).__name__}: {e}"

    # ---- individual tools ----------------------------------------------- #
    def _tool_list_dir(self, args) -> str:
        p = self._resolve(args.get("path", "."))
        if not p.exists():
            return f"[error] no such path: {args.get('path', '.')}"
        if p.is_file():
            return f"{p.name} (file, {p.stat().st_size} bytes)"
        entries = []
        for c in sorted(p.iterdir()):
            entries.append(f"{c.name}/" if c.is_dir() else c.name)
        return "\n".join(entries) or "(empty)"

    def _tool_read_file(self, args) -> str:
        p = self._resolve(args["path"])
        if not p.is_file():
            return f"[error] not a file: {args['path']}"
        text = p.read_text(errors="replace")
        if len(text) > _MAX_READ:
            text = text[:_MAX_READ] + f"\n... [truncated {len(text) - _MAX_READ} chars]"
        return text

    def _tool_run_command(self, args) -> str:
        res = run(args["command"], cwd=str(self.root), timeout_s=self.timeout_s)
        out = f"$ {res.command}\nexit={res.exit_code} ({res.duration_s}s)\n"
        if res.stdout:
            out += f"--- stdout ---\n{res.stdout}\n"
        if res.stderr:
            out += f"--- stderr ---\n{res.stderr}\n"
        return out.strip()

    def _tool_detect_build_system(self, args) -> str:
        self._det = _detect.detect(str(self.root))
        return json.dumps(to_dict(self._det), indent=2)

    def _tool_build(self, args) -> str:
        step = steps.build(str(self.root), self._detect_result(), timeout_s=self.timeout_s)
        return json.dumps(to_dict(step), indent=2)

    def _tool_test(self, args) -> str:
        step = steps.test(str(self.root), self._detect_result(), timeout_s=self.timeout_s)
        return json.dumps(to_dict(step), indent=2)

    # ---- edit tools (only offered when allow_edit=True) ----------------- #
    def _is_test_path(self, rel: str) -> bool:
        parts = {seg.lower() for seg in Path(rel).parts}
        name = Path(rel).name.lower()
        return bool(parts & set(_TEST_MARKERS)) or name.startswith("test_") or name.endswith("_test.py")

    def _record(self, rel: str, action: str, diff: str) -> None:
        self.changes.append({"path": rel, "action": action, "diff": diff, "is_test": self._is_test_path(rel)})

    def _tool_edit_file(self, args) -> str:
        rel = args["path"]
        p = self._resolve(rel)
        if not p.is_file():
            return f"[error] not a file: {rel}"
        old, new = args["old_str"], args["new_str"]
        before = p.read_text(errors="replace")
        count = before.count(old)
        if count == 0:
            return f"[error] old_str not found in {rel} (nothing changed)"
        if count > 1:
            return f"[error] old_str matched {count} times in {rel}; make it more specific so it matches exactly once"
        after = before.replace(old, new, 1)
        p.write_text(after)
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=rel, tofile=rel))
        self._record(rel, "edit", diff)
        warn = "  [warning] this is a TEST file — do not weaken tests to pass" if self._is_test_path(rel) else ""
        return f"edited {rel} (1 replacement){warn}"

    def _tool_write_file(self, args) -> str:
        rel = args["path"]
        p = self._resolve(rel)
        existed = p.is_file()
        before = p.read_text(errors="replace") if existed else ""
        after = args["content"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(after)
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=rel if existed else "/dev/null", tofile=rel))
        self._record(rel, "overwrite" if existed else "create", diff)
        warn = "  [warning] this is a TEST file — do not weaken tests to pass" if self._is_test_path(rel) else ""
        return f"{'overwrote' if existed else 'created'} {rel}{warn}"

    # ---- docker tools (only offered when allow_docker=True) ------------- #
    def _tool_suggest_docker_draft(self, args) -> str:
        arts = docker_gen.generate(str(self.root), self._detect_result())
        return (f"# Reference draft (adapt as needed; not written to disk).\n"
                f"# Write your final versions to {docker_gen.OUT_DIR}/Dockerfile and "
                f"{docker_gen.OUT_DIR}/run.sh with write_file.\n\n"
                f"===== {docker_gen.OUT_DIR}/Dockerfile =====\n{arts.dockerfile}\n"
                f"===== {docker_gen.OUT_DIR}/run.sh =====\n{arts.run_sh}")

    def _tool_run_in_docker(self, args) -> str:
        step = docker_run.build_and_run(str(self.root), self.image_tag, timeout_s=self.timeout_s)
        return json.dumps(to_dict(step), indent=2)

    # ---- numeric-correctness tools (only when allow_numeric=True) -------- #
    def _tool_check_numeric_close(self, args) -> str:
        rep = _numeric.allclose_report(
            args["actual"], args["expected"],
            rtol=args.get("rtol", 1e-7), atol=args.get("atol", 0.0))
        return json.dumps(rep, indent=2)

    def _tool_check_convergence(self, args) -> str:
        rep = _numeric.convergence_order(
            args["sizes"], args["errors"], expected_order=args.get("expected_order"))
        return json.dumps(rep, indent=2)

    def _tool_check_reproducibility(self, args) -> str:
        rep = _numeric.reproducibility(
            args["command"], cwd=str(self.root),
            runs=int(args.get("runs", 3)), timeout_s=self.timeout_s)
        return json.dumps(rep, indent=2)

    def _tool_report_result(self, args) -> str:
        self.finished = {"verdict": args.get("verdict", "fail"), "summary": args.get("summary", "")}
        return "acknowledged"

"""The reproducibility probe: answer Q1-Q7 about a PR by document-RAG.

Each question carries (a) a retrieval query and the source types most likely to
answer it, and (b) a JSON schema for its answer. We retrieve the top chunks for
the question from the :class:`~numbugminer.docstore.DocIndex`, hand them to the
chosen provider as grounded context, and require a schema-constrained JSON
answer. Nothing is answered from the model's prior — only from retrieved
evidence — and every answer records the chunk refs it was grounded on.

Q1  is this PR fixing a numerical bug?  (+ E1/E2/E3 sub-evidence)
Q2  what kind of numerical bug?
Q3  how is the repo built from source at this commit?
Q4  how are tests run, and the exact selector for the added/modified test?
Q5  which dependency versions, and by which resolution source?
Q6  what numerical environment settings (BLAS/compiler/flags/threads/dtype)?
Q7  stated vs implied cause, fix signature, trigger regime, do they agree?
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import providers
from .docstore import DocIndex
from .pr_source import PRBundle

BUG_KINDS = [
    "precision_loss", "catastrophic_cancellation", "overflow_underflow",
    "nan_inf_propagation", "convergence_failure", "dtype_promotion",
    "reduction_order_dependence", "tolerance_misuse", "algorithmic_instability",
    "symbolic_logical", "not_numerical",
]
_RESOLUTION = ["lockfile_at_commit", "ci_matrix_at_commit", "latest_before_merge", "unknown"]

_YESNO = {"type": "object", "additionalProperties": False,
          "properties": {"answer": {"type": "boolean"}, "evidence": {"type": "string"}},
          "required": ["answer", "evidence"]}


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    query: str
    sources: tuple[str, ...]
    schema: dict
    k: int = 6


QUESTIONS: list[Question] = [
    Question(
        "Q1", "Is this pull request fixing a numerical bug?",
        "floating point numerical bug NaN infinity overflow underflow precision "
        "tolerance failure non-convergence fix",
        ("pr_body", "issue", "discussion", "diff", "test_diff"),
        {"type": "object", "additionalProperties": False,
         "properties": {
             "is_numerical_bug": {"type": "boolean"},
             "confidence": {"type": "number"},
             "e1_modifies_fp_code": _YESNO,
             "e2_reports_numerical_symptom": _YESNO,
             "e3_changes_fp_behavior": _YESNO,
             "rationale": {"type": "string"}},
         "required": ["is_numerical_bug", "confidence", "e1_modifies_fp_code",
                      "e2_reports_numerical_symptom", "e3_changes_fp_behavior", "rationale"]}),
    Question(
        "Q2", "What kind of numerical bug is this?",
        "classify numerical bug type precision cancellation overflow nan convergence "
        "dtype promotion reduction order tolerance instability symbolic",
        ("pr_body", "issue", "diff", "discussion"),
        {"type": "object", "additionalProperties": False,
         "properties": {"bug_kind": {"type": "string", "enum": BUG_KINDS},
                        "confidence": {"type": "number"},
                        "rationale": {"type": "string"}},
         "required": ["bug_kind", "confidence", "rationale"]}),
    Question(
        "Q3", "How is this repository built from source at this commit?",
        "build from source install system packages build commands python version "
        "compile meson setup pyproject",
        ("readme", "ci_yaml", "lockfile"),
        {"type": "object", "additionalProperties": False,
         "properties": {"system_packages": {"type": "array", "items": {"type": "string"}},
                        "build_commands": {"type": "array", "items": {"type": "string"}},
                        "python_version": {"type": "string"},
                        "confidence": {"type": "number"}},
         "required": ["system_packages", "build_commands", "python_version", "confidence"]},
        k=8),
    Question(
        "Q4", "How are tests run, and what is the exact selector for the test added/modified by this PR?",
        "run tests pytest command test selector added modified test function file",
        ("ci_yaml", "readme", "test_diff", "diff"),
        {"type": "object", "additionalProperties": False,
         "properties": {"test_command": {"type": "string"},
                        "test_selector": {"type": "string"},
                        "framework": {"type": "string"},
                        "confidence": {"type": "number"}},
         "required": ["test_command", "test_selector", "framework", "confidence"]},
        k=8),
    Question(
        "Q5", "Which dependency versions were in use at this commit, and which resolution source was used?",
        "dependency versions numpy scipy pinned lockfile requirements CI matrix versions",
        ("lockfile", "ci_yaml"),
        {"type": "object", "additionalProperties": False,
         "properties": {"resolution_source": {"type": "string", "enum": _RESOLUTION},
                        "dependencies": {"type": "array", "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {"name": {"type": "string"}, "version": {"type": "string"}},
                            "required": ["name", "version"]}},
                        "confidence": {"type": "number"}},
         "required": ["resolution_source", "dependencies", "confidence"]},
        k=8),
    Question(
        "Q6", "What numerical environment settings are specified for this build?",
        "BLAS LAPACK MKL OpenBLAS compiler gcc clang optimization flags fast-math "
        "OMP_NUM_THREADS threads CUDA GPU default dtype float32 float64",
        ("ci_yaml", "lockfile", "readme"),
        {"type": "object", "additionalProperties": False,
         "properties": {"blas_lapack": {"type": "string"}, "compiler": {"type": "string"},
                        "optimization_flags": {"type": "string"}, "fast_math": {"type": "string"},
                        "thread_settings": {"type": "string"}, "gpu_toolkit": {"type": "string"},
                        "default_dtype": {"type": "string"}, "confidence": {"type": "number"}},
         "required": ["blas_lapack", "compiler", "optimization_flags", "fast_math",
                      "thread_settings", "gpu_toolkit", "default_dtype", "confidence"]},
        k=8),
    Question(
        "Q7", "What is the stated vs implied cause, the fix signature, the trigger regime, and do the causes agree?",
        "root cause of the bug stated in issue discussion; fix approach; input values "
        "that trigger the failure; edge case regime",
        ("issue", "discussion", "pr_body", "diff"),
        {"type": "object", "additionalProperties": False,
         "properties": {"stated_cause": {"type": "string"}, "implied_cause": {"type": "string"},
                        "fix_signature": {"type": "string"}, "trigger_input_regime": {"type": "string"},
                        "causes_agree": {"type": "boolean"}, "confidence": {"type": "number"}},
         "required": ["stated_cause", "implied_cause", "fix_signature",
                      "trigger_input_regime", "causes_agree", "confidence"]}),
]

_SYSTEM = (
    "You are a numerical-software reproducibility analyst. Answer ONLY from the "
    "retrieved CONTEXT passages provided; do not use outside knowledge. If the "
    "context does not support an answer, use empty strings/arrays and a low "
    "confidence rather than guessing. A numerical bug is a defect where "
    "floating-point computation goes wrong (NaN/Inf/overflow/underflow/"
    "divide-by-zero/precision loss/tolerance failure/non-convergence/instability) "
    "— not an environment, dependency, type-annotation, or pure-logic fault. "
    "Return a single JSON object matching the requested schema."
)


# --- deterministic evidence extractors (seed Q4/Q5 with ground truth) --------

_ADDED_DEF = re.compile(r"^\+\s*(?:async\s+)?def\s+(test_\w+)", re.M)
_ADDED_CLASS = re.compile(r"^\+\s*class\s+(Test\w+)", re.M)


def added_test_symbols(bundle: PRBundle) -> list[str]:
    """`path::symbol` for every test function/class the PR ADDS (from the diff)."""
    from .pr_source import is_test_file

    out: list[str] = []
    for f in bundle.files:
        fn = f.get("filename", "")
        if not is_test_file(fn):
            continue
        patch = f.get("patch", "") or ""
        out += [f"{fn}::{m.group(1)}" for m in _ADDED_CLASS.finditer(patch)]
        out += [f"{fn}::{m.group(1)}" for m in _ADDED_DEF.finditer(patch)]
    return out


# name + version-spec in requirements/constraints/pyproject/setup and conda envs
_PIN_REQ = re.compile(r"^\s*([A-Za-z][\w.\-]+)\s*(==|~=|>=|<=|!=)\s*([0-9][\w.\-*]*)", re.M)
_PIN_QUOTED = re.compile(r"['\"]([A-Za-z][\w.\-]+)\s*(==|~=|>=|<=|!=)\s*([0-9][\w.\-*]*)")
_PIN_CONDA = re.compile(r"^\s*-\s*([A-Za-z][\w.\-]+)\s*=(=?)\s*([0-9][\w.\-*]+)", re.M)
_EXACT_LOCKS = ("poetry.lock", "Pipfile.lock", "uv.lock")


def parse_dependency_pins(repo_files: list) -> tuple[list[dict], str]:
    """Extract (name, version-spec) pins from manifests and infer the resolution
    source. Deterministic — the model reports these rather than reading raw files."""
    pins: dict[str, str] = {}
    have_exact_lock = False
    for rf in repo_files or []:
        if rf.role != "lockfile":
            continue
        if rf.path in _EXACT_LOCKS:
            have_exact_lock = True
        rx = _PIN_CONDA if rf.path.endswith((".yml", ".yaml")) else (
            _PIN_QUOTED if rf.path.endswith((".toml", ".py", ".cfg")) else _PIN_REQ)
        for m in rx.finditer(rf.text):
            name, op, ver = m.group(1), (m.group(2) or "=="), m.group(3)
            pins.setdefault(name.lower(), f"{op}{ver}")
    exact = any(v.startswith("==") for v in pins.values())
    source = ("lockfile_at_commit" if (have_exact_lock or exact)
              else "ci_matrix_at_commit" if any(rf.role == "ci_yaml" for rf in (repo_files or []))
              else "unknown")
    return [{"name": n, "version": v} for n, v in sorted(pins.items())], source


def _extra_context(qid: str, bundle: PRBundle, repo_files: list) -> str:
    if qid == "Q4":
        syms = added_test_symbols(bundle)
        if syms:
            return ("ADDED/MODIFIED TEST SYMBOLS in this PR (build the exact selector from "
                    "these, e.g. `pytest <path>::<symbol>`):\n" + "\n".join(syms[:25]))
    if qid == "Q5":
        pins, source = parse_dependency_pins(repo_files)
        if pins or source != "unknown":
            body = "\n".join(f"{p['name']} {p['version']}" for p in pins[:50]) or "(no pinned specs found)"
            return (f"EXTRACTED DEPENDENCY SPECS from manifests at this commit "
                    f"(inferred resolution_source: {source}):\n{body}")
    return ""


@dataclass
class Answer:
    question_id: str
    question: str
    data: dict
    grounded_on: list[str] = field(default_factory=list)
    error: str = ""


def _context(hits) -> tuple[str, list[str]]:
    blocks, refs = [], []
    for ch, score in hits:
        blocks.append(f"[{ch.source}:{ch.ref} sim={score:.2f}]\n{ch.text}")
        refs.append(f"{ch.source}:{ch.ref}")
    return "\n\n---\n\n".join(blocks), refs


def answer_question(q: Question, index: DocIndex, *, provider: str, model: str,
                    host: str = providers.OLLAMA_HOST, extra_context: str = ""):
    hits = index.search(q.query, k=q.k, sources=q.sources)
    context, refs = _context(hits)
    extra = f"\n\nEXTRACTED FACTS (authoritative — prefer over prose):\n{extra_context}" if extra_context else ""
    user = (f"QUESTION: {q.text}\n\nCONTEXT PASSAGES:\n{context}{extra}\n\n"
            "Answer the question as a JSON object per the schema, grounded only in the "
            "context and extracted facts above.")
    usage = None
    try:
        res = providers.chat(provider, model, _SYSTEM, user, schema=q.schema, host=host)
        usage = res.usage
        data = providers.extract_json(res.text)
        return Answer(q.id, q.text, data, refs), usage
    except Exception as e:  # noqa: BLE001
        return Answer(q.id, q.text, {}, refs, error=f"{type(e).__name__}: {e}"), usage


@dataclass
class ProbeReport:
    reference: str
    repo: str
    buggy_commit: str
    fixed_commit: str
    provider: str
    model: str
    answers: list[Answer] = field(default_factory=list)
    n_chunks: int = 0
    usage: dict | None = None
    note: str = ""
    workdir: str = ""   # workspace/{repo}_{PR} — where the clone + report live
    test_patch: dict | None = None  # {applied, files, error} when --apply-test-patch

    def to_dict(self) -> dict:
        return {
            "reference": self.reference, "repo": self.repo,
            "buggy_commit": self.buggy_commit, "fixed_commit": self.fixed_commit,
            "provider": self.provider, "model": self.model,
            "n_chunks": self.n_chunks, "usage": self.usage, "note": self.note,
            "workdir": self.workdir, "test_patch": self.test_patch,
            "answers": [{"question_id": a.question_id, "question": a.question,
                         "answer": a.data, "grounded_on": a.grounded_on, "error": a.error}
                        for a in self.answers],
        }


def run_probe(bundle: PRBundle, repo_files: list, *, provider: str, model: str,
              embed_model: str | None = None, host: str = providers.OLLAMA_HOST,
              only: list[str] | None = None) -> ProbeReport:
    """Build the doc index from `bundle` + `repo_files`, then answer each question."""
    from .docstore import build_documents

    chunks = build_documents(bundle, repo_files)
    embedder = providers.make_embedder(provider, embed_model, host)
    index = DocIndex.build(embedder, chunks)

    report = ProbeReport(
        reference=bundle.ref.html_url, repo=f"{bundle.ref.owner}/{bundle.ref.repo}",
        buggy_commit=bundle.buggy_commit, fixed_commit=bundle.fixed_commit,
        provider=provider, model=model, n_chunks=len(chunks))

    in_tok = out_tok = 0
    cost = 0.0
    for q in QUESTIONS:
        if only and q.id not in only:
            continue
        extra = _extra_context(q.id, bundle, repo_files)
        ans, usage = answer_question(q, index, provider=provider, model=model,
                                     host=host, extra_context=extra)
        report.answers.append(ans)
        if usage:
            in_tok += usage.input_tokens; out_tok += usage.output_tokens
            cost += usage.cost_usd
    report.usage = {"model": model, "input_tokens": in_tok, "output_tokens": out_tok,
                    "cost_usd": round(cost, 6)}
    return report


def probe_pr(cfg, reference: str, *, repo: str | None = None, provider: str,
             model: str | None = None, embed_model: str | None = None,
             host: str = providers.OLLAMA_HOST, work_dir=None, clone: bool = True,
             require_merged: bool = True, apply_test_patch: bool = False,
             only: list[str] | None = None) -> ProbeReport:
    """End-to-end: fetch the PR bundle, clone the repo at the BUGGY commit, gather
    its README/CI/lock files, and run the Q1-Q7 probe over the document corpus."""
    from pathlib import Path

    from . import pr_source, repo as repo_mod
    from .config import PROJECT_ROOT

    model = model or providers.DEFAULT_CHAT_MODEL[provider]
    ref = pr_source.parse_pr_ref(reference, repo=repo)
    # Clone under workspace/{repo}_{PR}; --work-dir overrides. Known up front so
    # the report (and its saved JSON) always records where the run lives.
    dest = (Path(work_dir) if work_dir
            else PROJECT_ROOT / "workspace" / f"{ref.repo}_{ref.number}")

    bundle = pr_source.fetch_pr_bundle(ref, token=cfg.github_token,
                                       include_logs=False, include_commit_tests=False)
    if require_merged and not bundle.merged:
        return ProbeReport(reference=ref.html_url, repo=f"{ref.owner}/{ref.repo}",
                           buggy_commit=bundle.buggy_commit, fixed_commit=bundle.fixed_commit,
                           provider=provider, model=model, workdir=str(dest),
                           note=f"PR is {bundle.state or 'open'}, not merged — skipped. "
                                "Only merged PRs have a settled buggy/fixed commit pair.")

    repo_files: list = []
    note = ""
    test_patch = None
    if clone and bundle.buggy_commit:
        try:
            state = repo_mod.clone_and_reset(f"{ref.owner}/{ref.repo}", bundle.buggy_commit,
                                             dest, token=cfg.github_token)
            repo_files = state.files
            if apply_test_patch:
                # Apply ONLY the PR's test-file hunks onto the buggy checkout, so
                # the new test now fails against buggy code (exposes the bug).
                test_patch = repo_mod.apply_test_patch(dest, bundle.diff)
        except Exception as e:  # noqa: BLE001 — probe can still run on PR docs alone
            note = f"clone/reset failed ({type(e).__name__}: {e}); probing PR documents only."
    elif not clone:
        note = "clone disabled; probing PR documents only (no README/CI/lockfiles)."

    report = run_probe(bundle, repo_files, provider=provider, model=model,
                       embed_model=embed_model, host=host, only=only)
    report.workdir = str(dest)
    report.test_patch = test_patch
    report.note = "; ".join(x for x in (report.note, note) if x)
    return report


def _scalar(v) -> str:
    """Render one answer value as a compact comparable string."""
    if isinstance(v, dict):
        if "answer" in v:                      # yes/no evidence block
            return str(v.get("answer"))
        if "name" in v:                        # Q5 dependency entry
            return f"{v.get('name')} {v.get('version','')}".strip()
        return str(v)
    if isinstance(v, list):
        return "; ".join(_scalar(x) for x in v) if v else ""
    return "" if v is None else str(v)


def _flatten_answer(ans: dict) -> dict:
    """{field -> comparable string} for one answer's data (nested dicts/lists flattened)."""
    return {k: _scalar(v) for k, v in (ans.get("answer") or {}).items()}


def compare_reports(reports: list[dict]) -> dict:
    """Diff N probe report dicts field-by-field. Returns {labels, rows} where each
    row is {question_id, field, values:[per-report], agree:bool}."""
    labels = []
    for r in reports:
        model = (r.get("model") or "?").split(":")[0].replace("claude-", "").replace("-20251001", "")
        labels.append(f"{r.get('provider','?')}({model})")

    # answers indexed by question id, per report
    per = [{a["question_id"]: a for a in r.get("answers", [])} for r in reports]
    q_order = [q.id for q in QUESTIONS]
    seen_q = [qid for qid in q_order if any(qid in p for p in per)]

    rows = []
    for qid in seen_q:
        # union of fields for this question, preserving first-seen order
        fields: list[str] = []
        for p in per:
            if qid in p:
                for k in _flatten_answer(p[qid]):
                    if k not in fields:
                        fields.append(k)
        for f in fields:
            vals = []
            for p in per:
                a = p.get(qid)
                if a is None:
                    vals.append("—")
                elif a.get("error"):
                    vals.append(f"ERR")
                else:
                    vals.append(_flatten_answer(a).get(f, ""))
            present = [v for v in vals if v not in ("—", "ERR")]
            agree = len(present) == len(vals) and len(set(present)) == 1
            rows.append({"question_id": qid, "field": f, "values": vals, "agree": agree})
    costs = [(r.get("usage") or {}).get("cost_usd", 0.0) for r in reports]
    return {"labels": labels, "rows": rows, "costs": costs,
            "references": [r.get("reference", "") for r in reports]}


def format_comparison(comp: dict) -> str:
    """Render :func:`compare_reports` output as a fixed-width table."""
    labels, rows = comp["labels"], comp["rows"]
    w = max([len("field")] + [len(f"{r['question_id']} {r['field']}") for r in rows] + [0])
    cw = max([12] + [len(l) for l in labels] + [len(str(v)) for r in rows for v in r["values"]])
    cw = min(cw, 34)

    def cell(s):
        s = str(s)
        return (s[:cw - 1] + "…") if len(s) > cw else s.ljust(cw)

    out = [("  " + "field".ljust(w) + " | " + " | ".join(cell(l) for l in labels) + " | =")]
    out.append("  " + "-" * (w + 3 + (cw + 3) * len(labels)))
    cur = None
    for r in rows:
        if r["question_id"] != cur:
            cur = r["question_id"]
        name = f"{r['question_id']} {r['field']}"
        mark = "=" if r["agree"] else " "
        out.append("  " + name.ljust(w) + " | " +
                   " | ".join(cell(v) for v in r["values"]) + f" | {mark}")
    costs = comp.get("costs") or []
    if costs:
        out.append("  " + "cost_usd".ljust(w) + " | " +
                   " | ".join(cell(f"${c}") for c in costs) + " |")
    return "\n".join(out)


def save_report(report: ProbeReport, out_path=None) -> "Path":
    """Persist the probe report as JSON.

    Default location is a **sibling** of the clone dir (``workspace/{repo}_{PR}
    _probe_report.json``), deliberately OUTSIDE the git worktree — a report
    written inside ``workspace/{repo}_{PR}`` would be wiped by the ``git clean``
    in a later :func:`~numbugminer.repo.clone_and_reset` of the same PR.
    """
    import json
    from pathlib import Path

    if out_path:
        out = Path(out_path)
    else:
        wd = Path(report.workdir) if report.workdir else Path("workspace/probe")
        out = wd.parent / f"{wd.name}_probe_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return out

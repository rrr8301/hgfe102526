from __future__ import annotations

import json
import dataclasses
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from numagent import planning as _nag_planning
from numagent import repro_tools
from numagent.agent.llm.base import (build_client, complete_json,
                                     complete_json_turn)
from numagent.repro_session import run_session
from numagent.repro_workspace import BUGGY_DIR, FIXED_DIR

from NumericSpecificMiner.context import NumericalContext

from . import boundaries as B
from . import diagnose as D
from . import evidence as E
from . import package as PKG
from . import plan as P
from . import state as S
from . import verify as V

NAME = "numeric-pevr"

F2P_ONLY, FULL = "f2p_only", "full"
GRADING = {B.HBEC: F2P_ONLY, B.BRTG: F2P_ONLY}


@dataclass
class Trace:
    task: str
    plans: list = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    diagnoses: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    influence: list = field(default_factory=list)
    refusals: list = field(default_factory=list)
    provisioning_warning: str = ""
    tokens: dict = field(default_factory=dict)
    token_marks: list = field(default_factory=list)
    final: str = ""
    probe_agreement: object = None
    probes: list = field(default_factory=list)
    tolerance_window: dict = field(default_factory=dict)
    candidate_written: bool = False

    def as_dict(self) -> dict:
        return {"task": self.task, "plans": self.plans, "verdicts": self.verdicts,
                "diagnoses": self.diagnoses, "evidence": self.evidence,
                "evidence_influence": self.influence, "refusals": self.refusals,
                "provisioning_warning": self.provisioning_warning,
                "probes": self.probes,
                "candidate_written": self.candidate_written,
                "tolerance_window": self.tolerance_window,
                "tokens": self.tokens,
                "token_marks": self.token_marks,
                "final": self.final}


def _patch_cases(ctx) -> list:
    if getattr(ctx, "_patch_cases", None) is None:
        from numagent.planning import _enclosing_tests
        from numagent.repro_specs import test_case_pairs
        pairs = test_case_pairs(ctx.spec)
        if not pairs and getattr(ctx, "container", None) is not None:
            pairs = _enclosing_tests(ctx)
        ctx._patch_cases = list(pairs)
    return ctx._patch_cases


def _test_record(ctx, revision: str) -> dict | None:
    return (ctx.results.get(revision) or {}).get("test")


def _test_output(ctx, revision: str) -> str:
    record = _test_record(ctx, revision) or {}
    return (record.get("output") or "") + (record.get("stderr") or "")


_PROSE = re.compile(r"(^|/)(doc|docs|changelog|whatsnew|release[-_]notes?)(/|$)"
                    r"|\.(rst|md|txt|po)$", re.I)


def _is_prose(path: str) -> bool:
    return bool(_PROSE.search(path or ""))


def _patch_markers(spec, limit: int = 3) -> tuple:
    path = getattr(spec, "source_patch_path", "") or ""
    if not path or not Path(path).is_file():
        return [], []
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    plus = {ln[1:].strip() for ln in lines
            if ln.startswith("+") and not ln.startswith("+++")}
    minus = {ln[1:].strip() for ln in lines
             if ln.startswith("-") and not ln.startswith("---")}
    moved = plus & minus

    def collect(sign: str) -> list:
        out, target = [], ""
        for line in lines:
            if line.startswith("+++ ") or line.startswith("--- "):
                t = line[4:].strip()
                target = t[2:] if t[:2] in ("b/", "a/") else t
                continue
            if not line.startswith(sign):
                continue
            if not target or target == "/dev/null":
                continue
            if _is_prose(target):
                continue
            body = line[1:].strip()
            if body in moved or len(body) <= 25 or body.startswith(("#", "//", "*", "/*")):
                continue
            out.append((target, body))
        return out[:limit]

    return collect("-"), collect("+")


def _verify_revisions(ctx) -> str:
    quote = shlex.quote
    removed, added = _patch_markers(ctx.spec)
    problems = []

    def holds(rev: str, workdir: str, markers: list) -> tuple:
        present, checked, gone = 0, 0, []
        for target, body in markers:
            cmd = (f"test -f {quote(target)} && "
                   f"(grep -Fq {quote(body)} {quote(target)} && echo HAS || echo LACKS) "
                   f"|| echo NOFILE")
            res = repro_tools.run_shell_command(ctx, cmd, working_dir=workdir,
                                                purpose="inspect")
            said = (res.get("stdout_tail") or "").strip().splitlines()
            said = said[-1].strip() if said else ""
            if said == "NOFILE":
                gone.append(target)
            elif said in ("HAS", "LACKS"):
                checked += 1
                present += said == "HAS"
        return present, checked, gone

    if removed:
        present, checked, gone = holds("buggy", BUGGY_DIR, removed)
        for target in dict.fromkeys(gone):
            problems.append(f"buggy: {target} is missing")
        if checked and not present:
            problems.append(
                f"buggy contains none of the {checked} line(s) the fix DELETES "
                f"(e.g. {removed[0][1][:60]}…) — this tree is not the buggy revision")
    if added:
        present, checked, gone = holds("fixed", FIXED_DIR, added)
        for target in dict.fromkeys(gone):
            problems.append(f"fixed: {target} is missing")
        if checked and not present:
            problems.append(
                f"fixed contains none of the {checked} line(s) the fix ADDS "
                f"(e.g. {added[0][1][:60]}…) — this tree is not the fixed revision")

    try:
        files = sorted({f for f, _ in _patch_cases(ctx)})
    except Exception:
        files = []
    for target in files[:3]:
        for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
            res = repro_tools.run_shell_command(
                ctx, f"test -f {quote(target)} && echo present || echo MISSING",
                working_dir=workdir, purpose="inspect")
            if "MISSING" in (res.get("stdout_tail") or ""):
                problems.append(f"{rev}: reproducing test {target} is missing")
    return "; ".join(dict.fromkeys(problems))


_K_ARG = re.compile(r"(-k\s+)(['\"]?)([A-Za-z_]\w*)\2")
_PY_PATH = re.compile(r"\S+\.py\b")
_PYARGS = re.compile(r"(--pyargs\s+)(\S+)")


def _recase(template: str, test_file: str, case: str) -> str:
    cmd = _K_ARG.sub(lambda m: f"{m.group(1)}{case}", template, count=1)
    if "-k " not in cmd:
        cmd += f" -k {case}"
    if "--pyargs" in cmd:
        module = test_file[:-3].replace("/", ".") if test_file.endswith(".py") else test_file
        cmd = _PYARGS.sub(lambda m: f"{m.group(1)}{module}", cmd, count=1)
    elif test_file:
        cmd = _PY_PATH.sub(test_file, cmd, count=1)
    return cmd


def _cases_tried(ctx, pairs: list) -> set:
    ran = []
    for rev in ("buggy", "fixed"):
        for rec in ((ctx.results.get(rev) or {}).get("commands") or []):
            ran.append(str(rec.get("command") or ""))
    text = " ".join(ran)
    return {case for _, case in pairs if case and case in text}


def _hang_expected(context) -> bool:
    manifestation = ((getattr(context, "numerical_failure", {}) or {})
                     .get("manifestation") or "")
    return str(manifestation).strip().lower() in ("hang", "hangs", "non-termination",
                                                  "infinite_loop", "timeout")


def _widen_to_other_cases(ctx, verdict, trace, task: str, hang: bool = False):
    if task != B.HBEC:
        return verdict
    info = getattr(ctx, "workspace_info", None) or {}
    if info.get("build_mode") == "agent" and not all(
            ((info.get("build") or {}).get(r) or {}).get("built") for r in ("buggy", "fixed")):
        return verdict
    try:
        pairs = _patch_cases(ctx)
    except Exception:
        return verdict
    tried = _cases_tried(ctx, pairs)
    remaining = [(f, c) for f, c in pairs if c not in tried]
    if hang and not remaining:
        remaining = list(pairs)
        tried = tried or {c for _, c in pairs}
    if not remaining or not tried:
        return verdict
    template = ((_test_record(ctx, "buggy") or {}).get("command")
                or (_test_record(ctx, "fixed") or {}).get("command") or "")
    if not template:
        return verdict
    ctx.emit("thought",
             f"{verdict.reproduction} on '{', '.join(sorted(tried))}' — that case "
             f"cannot discriminate. Trying the patch's other case(s): "
             f"{', '.join(c for _, c in remaining)}")
    for test_file, case in remaining[:6]:
        cmd = _recase(template, test_file, case)
        for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
            if "--pyargs" in cmd:
                _ensure_pyargs_plugin(ctx, rev)
            repro_tools.run_shell_command(ctx, cmd, working_dir=workdir,
                                          purpose="test")
        fresh = V.classify(_test_record(ctx, "buggy"), _test_record(ctx, "fixed"),
                           hang_expected=hang, context=getattr(ctx, "context", None))
        trace.verdicts.append(fresh.as_dict())
        ctx.emit("tool", {"name": "verify_numerical_outcome",
                          "status": fresh.reproduction, "observed": fresh.as_dict()})
        if fresh.reproduction == V.F2P and fresh.valid:
            ctx.emit("thought", f"'{case}' discriminates: {fresh.reason}")
            return fresh
        ctx.emit("thought", f"'{case}' is {fresh.reproduction} — it does not "
                            f"discriminate either")
    return verdict


_WROTE_TEST = re.compile(r"[\w./-]*test[\w./-]*\.py")


def _generated_test_path(ctx) -> str:
    seen = []
    for rev in ("buggy", "fixed"):
        for rec in ((ctx.results.get(rev) or {}).get("commands") or []):
            cmd = str(rec.get("command") or "")
            if "pytest" not in cmd:
                continue
            for hit in _WROTE_TEST.findall(cmd):
                if "/tests/test_" in hit and "brtg" not in hit.lower() \
                        and "gen" not in hit.lower() and "candidate" not in hit.lower():
                    continue
                seen.append(hit)
    return seen[-1] if seen else ""


def _mirror_generated_test(ctx, plan=None) -> str:
    path = ""
    if plan is not None:
        written = [w[0] for w in plan.writes()]
        path = written[-1] if written else ""
    path = path or _generated_test_path(ctx)
    if not path:
        return "no generated test to run — the plan named none"
    quote = shlex.quote
    have = {}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        probe = repro_tools.run_shell_command(
            ctx, f"test -f {quote(path)} && echo HAS || echo MISSING",
            working_dir=workdir, purpose="inspect")
        have[workdir] = "HAS" in (probe.get("stdout_tail") or "")
    if not any(have.values()):
        return (f"the generated test {path} exists on NEITHER revision — the plan "
                f"named it but never wrote it")
    if all(have.values()):
        src = dst = ""
    else:
        src = BUGGY_DIR if have[BUGGY_DIR] else FIXED_DIR
        dst = FIXED_DIR if have[BUGGY_DIR] else BUGGY_DIR
    if dst:
        repro_tools.run_shell_command(
            ctx, f"mkdir -p \"$(dirname {quote(path)})\" && "
                 f"cp {src}/{quote(path)} {quote(path)}",
            working_dir=dst, purpose="setup")
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        work = f"/tmp/numbugbench-live-{rev}"
        package = _package_under_test(ctx)
        prefix = _import_prefix(ctx, rev, package)
        if prefix or _importable_from_tmp(ctx, rev, package):
            command = (f"rm -rf {work} && mkdir -p {work} && "
                       f"cp {quote(path)} {work}/test_candidate.py && "
                       f"cd {work} && {prefix}python -m pytest test_candidate.py "
                       f"-q -rA -p no:randomly")
        else:
            command = (f"{prefix}python -m pytest {quote(path)} -q -rA "
                       f"-p no:randomly")
            ctx.emit("thought", f"{rev}: running the candidate INSIDE the "
                                f"checkout — a neutral directory cannot import "
                                f"{package} in this image")
        repro_tools.run_shell_command(ctx, command, working_dir=workdir,
                                      purpose="test")
    where = f"from {src} to {dst}" if dst else "(already on both revisions)"
    return (f"ran the generated test {path} on both revisions {where} — a BRTG "
            f"candidate is a claim about two revisions")


def _test_identity(ctx, task: str) -> str:
    if task != B.HBEC:
        return ("THE TEST: you must WRITE it. The developer's test is withheld "
                "for this task, so address a file you create yourself.")
    try:
        pairs = _patch_cases(ctx)
    except Exception:
        pairs = []
    if not pairs:
        return ""
    by_file: dict = {}
    for test_file, case in pairs[:12]:
        by_file.setdefault(test_file, []).append(case)
    lines = [f"  {f}   cases: {', '.join(cases)}" for f, cases in by_file.items()]
    first_file, first_cases = next(iter(by_file.items()))
    return (
        "THE TEST TO MAKE TRANSITION (from the reproducing-test patch, already "
        "applied in both revisions):\n" + "\n".join(lines) + "\n"
        "Select a case with `-k`, together with the FILE PATH:\n"
        f"  python -m pytest {first_file} -k {first_cases[0]} -q\n"
        "Two ways of writing this do not work. A `file::case` node id will NOT "
        "resolve when the case is a METHOD on a test class — which it often is, "
        "and the class name is not shown here — while `-k` matches either way. "
        "And `-k` WITHOUT the file path collects the entire suite, which pulls in "
        "unrelated modules and dies on their missing imports.")


def _token_usage(client, budget) -> dict:
    plan_usage = dict(getattr(client, "usage", {}) or {})
    ev_in = sum(int(e.get("input_tokens") or 0) for e in (budget.log or []))
    ev_out = sum(int(e.get("output_tokens") or 0) for e in (budget.log or []))
    ev_cost = sum(float(e.get("cost_usd") or 0.0) for e in (budget.log or []))
    return {
        "model": plan_usage.get("model"),
        "planning": {"calls": plan_usage.get("calls", 0),
                     "input_tokens": plan_usage.get("input_tokens", 0),
                     "output_tokens": plan_usage.get("output_tokens", 0),
                     "cache_read_tokens": plan_usage.get("cache_read_tokens", 0),
                     "cache_write_tokens": plan_usage.get("cache_write_tokens", 0),
                     "cost_usd": round(float(plan_usage.get("cost_usd") or 0.0), 6)},
        "evidence": {"calls": len(budget.log or []), "input_tokens": ev_in,
                     "output_tokens": ev_out, "cost_usd": round(ev_cost, 6)},
        "total": {
            "calls": int(plan_usage.get("calls", 0)) + len(budget.log or []),
            "input_tokens": int(plan_usage.get("input_tokens", 0)) + ev_in,
            "output_tokens": int(plan_usage.get("output_tokens", 0)) + ev_out,
            "cost_usd": round(float(plan_usage.get("cost_usd") or 0.0) + ev_cost, 6),
        },
    }


def _mark_tokens(trace, client, budget, iteration: int) -> None:
    now = _token_usage(client, budget).get("total") or {}
    prev = (trace.token_marks[-1] if trace.token_marks else {}) or {}
    tok = int(now.get("input_tokens") or 0) + int(now.get("output_tokens") or 0)
    trace.token_marks.append({
        "iteration": iteration,
        "replan": max(0, iteration - 1),
        "calls": int(now.get("calls") or 0),
        "tokens_cumulative": tok,
        "tokens_this_iteration": tok - int(prev.get("tokens_cumulative") or 0),
        "cost_usd_cumulative": round(float(now.get("cost_usd") or 0.0), 6),
        "cost_usd_this_iteration": round(
            float(now.get("cost_usd") or 0.0)
            - float(prev.get("cost_usd_cumulative") or 0.0), 6),
    })


def _package_under_test(ctx) -> str:
    repo = str(getattr(ctx.spec, "repo", "") or "")
    return repo.split("/")[-1] if "/" in repo else repo


def _import_name(ctx) -> str:
    from numagent.repro_specs import python_package
    return python_package(ctx.spec)


def _dev_tests(ctx) -> tuple:
    try:
        pairs = _patch_cases(ctx)
    except Exception:
        return ()
    out = {f for f, _ in pairs} | {c for _, c in pairs}
    return tuple(sorted(x for x in out if x))


_REFUSED = "CANDIDATE REFUSED BEFORE RUNNING: "
_BUILD_FAILED = "BUILD FAILED BEFORE ANY TEST RAN: "

_CONFLICT_BLOCK = re.compile(r"The conflict is caused by:\n(?:[ \t]+\S.*\n?)+")


def _build_reason(res: dict) -> str:
    from numagent.repro_compare import first_error_line
    output = res.get("output") or ""
    why = res.get("error") or first_error_line(output) or output[-400:].strip() or "no output"
    block = _CONFLICT_BLOCK.search(output)
    if block:
        why += " | " + " ; ".join(ln.strip() for ln in block.group(0).splitlines() if ln.strip())
    return why[:900]


def _apply_writes(ctx, plan: P.Plan, trace: Trace, task: str) -> str:
    from numagent.repro_container import write_file

    refused = ""
    for path, body, revision in plan.writes():
        allowed = B.check_artifact(task, path)
        if not allowed.allowed:
            trace.refusals.append({"command": f"write {path}", "reason": allowed.reason})
            ctx.emit("thought", f"REFUSED ({task} boundary): {allowed.reason}")
            continue
        if task == B.BRTG:
            well_formed = B.check_candidate(body)
            already = sum(1 for r in trace.refusals
                          if r.get("reason") == well_formed.reason)
            if not well_formed.allowed and not already:
                trace.refusals.append({"command": f"write {path}",
                                       "reason": well_formed.reason})
                ctx.emit("thought", f"REFUSED before running: {well_formed.reason}")
                refused = well_formed.reason
                continue
            if not well_formed.allowed:
                ctx.emit("thought", f"WARNING (running anyway, already said "
                                    f"once): {well_formed.reason}")
        targets = ([BUGGY_DIR, FIXED_DIR] if task == B.BRTG or revision == "both"
                   else [FIXED_DIR if revision == "fixed" else BUGGY_DIR])
        written = []
        for workdir in targets:
            repro_tools.run_shell_command(
                ctx, f"mkdir -p \"$(dirname {shlex.quote(path)})\"",
                working_dir=workdir, purpose="setup")
            try:
                write_file(ctx.container, f"{workdir}/{shlex.quote(path)}", body)
            except Exception as e:
                ctx.emit("thought", f"could not write {workdir}/{path}: "
                                    f"{type(e).__name__}: {e}")
                continue
            check = repro_tools.run_shell_command(
                ctx, f"test -s {shlex.quote(path)} && echo OK || echo EMPTY",
                working_dir=workdir, purpose="inspect")
            if "OK" in (check.get("stdout_tail") or ""):
                written.append(workdir)
        if written and task == B.BRTG:
            trace.candidate_written = True
        ctx.emit("thought", f"wrote {path} ({len(body)} chars) to "
                            f"{', '.join(written) or 'NOWHERE — the write failed'}")


_PROBE = re.compile(r"^\s*cd\s+/tmp\s*&&\s*(?:python|python3)\s+-c\s", re.I)


def _is_probe(command: str, purpose: str) -> bool:
    if not _PROBE.match(command or ""):
        return False
    return not re.search(r"\b(?:pip|pytest|open\(|write|rmtree|unlink|remove)\b",
                         command or "")


def _import_rule(ctx, task: str) -> str:
    package = _package_under_test(ctx)
    if not package or task != B.BRTG:
        return ""
    parts = []
    for rev in ("buggy", "fixed"):
        prefix = _import_prefix(ctx, rev, package)
        workdir = FIXED_DIR if rev == "fixed" else BUGGY_DIR
        parts.append(f"  {rev}:  cd /tmp && {prefix}python -c "
                     f"\"import {_import_name(ctx)}; ...\""
                     f"{'' if prefix else '   (installed in the venv)'}")
    return ("HOW TO IMPORT THE PACKAGE IN A PROBE (measured in THIS image — use "
            "it exactly):\n" + "\n".join(parts))


_API_NOISE = frozenset("""
the and for with from into dtype type mapping map table value values output
input api function method module class attribute implementation used uses
test tests case cases numerical numeric
""".split())

_RESOLVE_PY = r"""
import importlib, pkgutil, sys
pkg, hints, names = sys.argv[1], sys.argv[2].split(","), sys.argv[3].split(",")
hints = [h for h in hints if h]
names = [n for n in names if n]
found = []
def consider(modname):
    try:
        m = importlib.import_module(modname)
    except BaseException:
        return
    for n in names:
        if hasattr(m, n):
            found.append(f"{modname}\t{n}")
# a dotted candidate may already be importable as given
for n in list(names):
    if "." in n:
        mod, _, leaf = n.rpartition(".")
        try:
            m = importlib.import_module(mod)
            if hasattr(m, leaf):
                found.append(f"{mod}\t{leaf}")
        except BaseException:
            pass
try:
    root = importlib.import_module(pkg)
except BaseException:
    print(""); raise SystemExit(0)
consider(pkg)
paths = getattr(root, "__path__", [])
for info in pkgutil.walk_packages(paths, prefix=pkg + "."):
    name = info.name
    low = name.lower()
    # Case-insensitive, and no depth cap once a hint matches. Both mattered:
    # cvxpy#3390's `compress_matrix` lives in
    # cvxpy.reductions.solvers.conic_solvers.cvxopt_conif, which a
    # case-sensitive "CVXOPT" test misses and a 3-dot depth cap skips.
    if hints and not any(h.lower() in low for h in hints):
        continue
    if ".tests" in low or low.endswith(".tests") or "/test" in low:
        continue
    if not hints and name.count(".") > 3:
        continue
    consider(name)
    if len(found) >= 4:
        break
seen, out = set(), []
for f in found:
    if f not in seen:
        seen.add(f); out.append(f)
print("|".join(out[:4]))
"""


def _api_candidates(affected_api) -> tuple:
    text = str(affected_api or "")
    symbols, hints = [], []
    for token in re.findall(r"[A-Za-z_][\w.]*", text):
        leaf = token.split(".")[-1]
        low = leaf.lower()
        if len(leaf) < 3 or low in _API_NOISE:
            continue
        if "." in token:
            symbols.append(token)
            hints.extend(part for part in token.split(".")[:-1]
                         if len(part) > 2 and part.lower() not in _API_NOISE)
        else:
            symbols.append(leaf)
            hints.append(leaf)
    symbols = sorted(dict.fromkeys(symbols), key=len, reverse=True)[:6]
    return tuple(symbols), tuple(dict.fromkeys(hints))[:4]


def _resolve_api_path(ctx, task: str, context) -> str:
    if task != B.BRTG:
        return ""
    nf = getattr(context, "numerical_failure", {}) or {}
    api = nf.get("affected_api") if isinstance(nf, dict) else ""
    symbols, hints = _api_candidates(api)
    if not symbols:
        return ""
    package = _package_under_test(ctx)
    module = _import_name(ctx)
    prefix = _import_prefix(ctx, "fixed", package)
    script = f"/tmp/numbugbench_resolve_{abs(hash(module)) % 10**6}.py"
    try:
        from numagent.repro_container import write_file
        write_file(ctx.container, script, _RESOLVE_PY)
    except Exception:
        return ""
    res = repro_tools.run_shell_command(
        ctx,
        f"cd /tmp && {prefix}python {script} {shlex.quote(module)} "
        f"{shlex.quote(','.join(hints))} {shlex.quote(','.join(symbols))}",
        working_dir=FIXED_DIR, purpose="inspect", timeout=180)
    hits = [h for h in ((res.get("stdout_tail") or "").strip().splitlines() or [""])[-1]
            .split("|") if h]
    if not hits:
        return ("WHERE THE AFFECTED API LIVES: could not be resolved by import in "
                "this image. Locate it yourself before asserting on it — search "
                "the checkout with grep for the definition, and import it by the "
                "path you find. Do not guess a module name: a wrong "
                "path raises AttributeError on BOTH revisions and the run scores "
                "nothing.")
    lines = []
    for hit in hits:
        module, _, symbol = hit.partition("\t")
        if symbol:
            lines.append(f"from {module} import {symbol}")
        else:
            lines.append(f"import {module}")
    return ("WHERE THE AFFECTED API LIVES (verified by importing it in THIS "
            "image — use exactly these import statements):\n  "
            + "\n  ".join(lines)
            + "\nThe last component is an ATTRIBUTE of that module, not a "
              "submodule: import it exactly as written above rather than "
              "guessing a module path.")


def _importable_from_tmp(ctx, revision: str, package: str) -> bool:
    if not package:
        return True
    workdir = FIXED_DIR if revision == "fixed" else BUGGY_DIR
    module = _import_name(ctx)
    probe = repro_tools.run_shell_command(
        ctx, f"cd /tmp && python -c 'import {module}' >/dev/null 2>&1 "
             f"&& echo YES || echo NO",
        working_dir=workdir, purpose="inspect")
    return "YES" in (probe.get("stdout_tail") or "")


def _import_prefix(ctx, revision: str, package: str) -> str:
    if not package:
        return ""
    workdir = FIXED_DIR if revision == "fixed" else BUGGY_DIR
    module = _import_name(ctx)
    probe = repro_tools.run_shell_command(
        ctx, f"cd /tmp && python -c 'import {module}' >/dev/null 2>&1 "
             f"&& echo INSTALLED || echo ABSENT",
        working_dir=workdir, purpose="inspect")
    if "INSTALLED" in (probe.get("stdout_tail") or ""):
        return ""
    confirm = repro_tools.run_shell_command(
        ctx, f"cd /tmp && PYTHONPATH={workdir} python -c 'import {module}' "
             f">/dev/null 2>&1 && echo WORKS || echo NO",
        working_dir=workdir, purpose="inspect")
    if "WORKS" not in (confirm.get("stdout_tail") or ""):
        ctx.emit("thought",
                 f"{revision}: `{package}` imports NEITHER from the venv nor "
                 f"from the checkout on PYTHONPATH — a neutral directory cannot "
                 f"run this revision at all, so commands must run inside the "
                 f"checkout. Not setting PYTHONPATH.")
        return ""
    ctx.emit("thought",
             f"{revision}: `{package}` is NOT installed in this revision's venv "
             f"but the checkout satisfies the import — using "
             f"PYTHONPATH={workdir} for candidate runs and probes")
    return f"PYTHONPATH={workdir} "


def _same_measurement(a: str, b: str) -> bool:
    norm = lambda t: " ".join((t or "").split())
    return bool(norm(a)) and norm(a) == norm(b)


def _mirror_probe(ctx, command: str, revision: str, purpose: str) -> str:
    other = "buggy" if revision == "fixed" else "fixed"
    workdir = FIXED_DIR if other == "fixed" else BUGGY_DIR
    prefix = _import_prefix(ctx, other, _package_under_test(ctx))
    if prefix and "PYTHONPATH=" not in command:
        command = command.replace("python -c", f"{prefix}python -c", 1)
    res = repro_tools.run_shell_command(ctx, command, working_dir=workdir,
                                        purpose=purpose)
    return ((res.get("stdout_tail") or "") + (res.get("stderr_tail") or "")).strip()
    return refused


def _ensure_pyargs_plugin(ctx, revision: str) -> None:
    done = ctx.__dict__.setdefault("_pyargs_installed", set())
    if revision in done:
        return
    workdir = FIXED_DIR if revision == "fixed" else BUGGY_DIR
    import base64 as _b64
    try:
        src = Path(__file__).resolve().parent.parent / "numagent" / "pytest_pyargs_conftests.py"
        blob = _b64.b64encode(src.read_bytes()).decode()
    except Exception:
        done.add(revision)
        return
    name = "numagent_pyargs_conftests"
    cmd = (
        'PURELIB="$(python -c '
        "\"import sysconfig;print(sysconfig.get_paths()['purelib'])\"" ' 2>/dev/null)"; '
        'test -n "$PURELIB" || exit 11; '
        f'echo {blob} | base64 -d > "$PURELIB/{name}.py" || exit 12; '
        f'D="$PURELIB/{name}-0.dist-info"; mkdir -p "$D" || exit 13; '
        f'printf "Metadata-Version: 2.1\\nName: {name.replace("_","-")}\\nVersion: 0\\n" > "$D/METADATA"; '
        f'printf "[pytest11]\\n{name} = {name}\\n" > "$D/entry_points.txt"; '
        'echo "PYARGS_PLUGIN_OK:$PURELIB"')
    res = repro_tools.run_shell_command(ctx, cmd, working_dir=workdir, purpose="setup")
    out = (res.get("stdout_tail") or "") + (res.get("stderr_tail") or "")
    if "PYARGS_PLUGIN_OK:" in out:
        done.add(revision)
        ctx.emit("thought", f"{revision}: provisioned the --pyargs conftest "
                            f"plugin into the active interpreter")
    else:
        ctx.emit("thought", f"{revision}: --pyargs plugin not provisioned yet "
                            f"(interpreter/site not resolvable); will retry")


def _run_plan(ctx, plan: P.Plan, trace: Trace, task: str) -> str:
    last_output = ""
    write_refusal = _apply_writes(ctx, plan, trace, task)
    if write_refusal:
        return _REFUSED + write_refusal
    agent = (ctx.workspace_info or {}).get("build_mode") == "agent"
    builds = (plan.agent_builds() if agent
              else [(r, v, p, "") for r, v, p in plan.rebuilds()])
    build_failures = []
    for revision, version, pins, recipe in builds:
        if task != B.HBEC:
            reason = f"{task} holds the environments fixed: rebuilding a revision is forbidden"
            trace.refusals.append({"command": f"rebuild {revision} {version} {pins}", "reason": reason})
            ctx.emit("thought", f"REFUSED ({task} boundary): {reason}")
            continue
        if agent and recipe not in ("", "compiled", "pure"):
            recipe = ""
        if agent and not recipe:
            recipe = ((ctx.workspace_info.get("build") or {}).get(
                "fixed" if revision == "fixed" else "buggy") or {}).get("recipe", "")
        for rev in (("buggy", "fixed") if revision == "both" else (revision,)):
            res = repro_tools.rebuild_revision(ctx, rev, version, pins=pins, recipe=recipe)
            if agent and not res.get("built"):
                build_failures.append(f"{rev} (recipe {recipe or '?'}, Python "
                                      f"{res.get('python_version')}): {_build_reason(res)}")
            got = ", ".join(f"{k}=={v}" for k, v in (res.get("era_pins") or {}).items())
            last_output = (f"rebuild {rev} on Python {res.get('python_version')}: "
                           + (f"ok, venv runs {res.get('venv_python', '?')}; pins {got}" if res.get("built")
                              else "FAILED — " + (res.get("error") or (res.get("output") or "")[-400:])))
    if build_failures:
        return _BUILD_FAILED + " | ".join(build_failures)
    package = _package_under_test(ctx)
    dev = _dev_tests(ctx) if task == B.BRTG else ()
    for command, revision, purpose in plan.commands():
        check = B.check_command(task, command, purpose=purpose, package=package,
                                dev_tests=dev)
        if check.allowed and agent and (purpose == "test" or "pytest" in command):
            rev = "fixed" if revision == "fixed" else "buggy"
            if not ((ctx.workspace_info.get("build") or {}).get(rev) or {}).get("built"):
                check = B.Check(False, f"{rev} has not been built: plan a \"rebuild\" "
                                       f"action with a recipe for it first — the "
                                       f"developer test runs only in an environment "
                                       f"the harness has verified")
        if not check.allowed:
            trace.refusals.append({"command": command[:160], "reason": check.reason})
            ctx.emit("thought", f"REFUSED ({task} boundary): {check.reason}")
            continue
        workdir = "/workspace/fixed" if revision == "fixed" else "/workspace/buggy"
        if purpose == "test" and "--pyargs" in command:
            _ensure_pyargs_plugin(ctx, "fixed" if revision == "fixed" else "buggy")
        res = repro_tools.run_shell_command(ctx, command, working_dir=workdir,
                                            purpose=purpose)
        last_output = (res.get("stdout_tail") or "") + (res.get("stderr_tail") or "")
        if task == B.BRTG and _is_probe(command, purpose):
            mine = last_output.strip()
            theirs = _mirror_probe(ctx, command, revision, purpose)
            other = "buggy" if revision == "fixed" else "fixed"
            trace.probe_agreement = _same_measurement(mine, theirs)
            buggy_text = mine if revision == "buggy" else theirs
            fixed_text = theirs if revision == "buggy" else mine
            window = V.tolerance_window(buggy_text, fixed_text)
            trace.probes.append({"command": command[:200], revision: mine[-300:],
                                 other: theirs[-300:],
                                 "agree": trace.probe_agreement,
                                 "tolerance_window": window})
            trace.tolerance_window = window or trace.tolerance_window
            ctx.emit("thought",
                     f"PROBE ON BOTH REVISIONS (a value is only evidence as a "
                     f"comparison)\n  {revision}: {mine[-400:]}\n  {other}: "
                     f"{theirs[-400:]}\n  -> if these two agree, this input does "
                     f"not discriminate and the INPUT must change; if they "
                     f"differ, assert the FIXED value."
                     + (f"\n  MEASURED TOLERANCE WINDOW: the two revisions "
                        f"differ by {window['absolute_gap']:.6g}, so an "
                        f"assertion discriminates only with a tolerance BELOW "
                        f"that — use atol={window['suggested_atol']:.3g}"
                        + (f" (or rtol={window['suggested_rtol']:.3g})"
                           if window.get("suggested_rtol") else "")
                        + f". A tighter tolerance than the fixed revision's own "
                          f"accuracy fails on BOTH sides; a looser one than "
                          f"{window['absolute_gap']:.6g} passes on both."
                        if window else ""))
    return last_output


_PLAN_ATTEMPTS = 3


def _plan_once(client, context, task, *, state=None, diagnosis=None,
               extra_evidence="", grading="", test_identity="",
               measurements=None, ctx=None) -> P.Plan:
    build_mode = ((getattr(ctx, "workspace_info", None) or {}).get("build_mode", "")
                  if ctx is not None else "")
    prompt = P.build_prompt(context, task, state=state, diagnosis=diagnosis,
                            extra_evidence=extra_evidence, grading=grading,
                            test_identity=test_identity,
                            measurements=measurements, build_mode=build_mode)
    schema = P.AGENT_PLAN_SCHEMA if build_mode == "agent" else P.PLAN_SCHEMA
    reason = ""
    for attempt in range(_PLAN_ATTEMPTS):
        raw, turn = complete_json_turn(
            client, P.SYSTEM, prompt, schema, "submit_plan",
            "Submit the structured plan.",
            max_tokens=8192 * (attempt + 1))
        if raw:
            return P.parse(raw, task)
        reason = (getattr(turn, "stop_reason", "") or "no content").strip()
        if ctx is not None:
            ctx.emit("thought", f"the planner returned no object "
                                f"(stop_reason={reason!r}); retrying "
                                f"{attempt + 1}/{_PLAN_ATTEMPTS}")
    return P.Plan(task=task,
                  error=f"planner returned no object after {_PLAN_ATTEMPTS} "
                        f"attempts (last stop_reason={reason!r})")


def _compile_deferred(ctx) -> bool:
    build = (getattr(ctx, "workspace_info", None) or {}).get("build") or {}
    revs = [r for r in build.values() if isinstance(r, dict) and "built" in r]
    if not revs:
        return False
    return any(r.get("compile_deferred") for r in revs) and         not all(r.get("built") for r in revs)


def _package_under_test(ctx) -> str:
    try:
        from numagent.repro_specs import python_package
        return python_package(ctx.spec) or ""
    except Exception:
        return ""


def _build_repair_diagnosis(ctx, task: str, context):
    info = getattr(ctx, "workspace_info", None) or {}
    need = info.get("build_repair_needed") or {}
    if not need or task != B.HBEC:
        return None, ""
    build = info.get("build") or {}
    if need.get("deferred") and info.get("build_mode") == "agent":
        logs = ("NOTHING HAS BEEN BUILT (build-mode agent): neither revision has an "
                "environment of its own yet, and the developer test is refused on a "
                "revision until it has one. Decide whether this package is COMPILED "
                "(extension modules that must be compiled before import) or PURE "
                "(imports from its source tree), from the context and the checkout. "
                "Then build BOTH revisions with one \"rebuild\" action, revision "
                "\"both\", recipe \"compiled\" or \"pure\", plus python_version and "
                "pins if the evidence calls for them. If you cannot tell yet, make "
                "this plan inspection only. Once both build, run the developer test "
                "the way the test identity above says; each revision's venv is "
                "already first on PATH, also after a `cd /tmp`.")
        reason = "nothing built: the agent builds both revisions (build-mode agent)"
    elif need.get("deferred"):
        logs = ("The COMPILE has not been run yet (build-in-loop mode); everything "
                "around it has. Each revision already HAS a virtualenv at "
                "<revision>/.venv, with the era-pinned dependencies, the project's "
                "build requirements and its pytest plugins installed into it, and a "
                "constraints file every later pip in that revision honours. What is "
                "missing is the package itself, so EVERY test will fail to import it "
                "until you build it — re-running the test without building first "
                "cannot do anything but repeat the same ImportError.\n"
                "YOUR FIRST ACTION IS A `rebuild` ACTION, ONE PER REVISION: buggy "
                "and fixed. That is the supported route and it compiles the revision "
                "into the venv already prepared for it, honouring the era pins. Use "
                "it before anything else; a hand-written "
                "`.venv/bin/pip install --no-build-isolation .` is a fallback for "
                "when a rebuild has failed, not the first thing to try. Never a bare "
                "`python` — run the test with `.venv/bin/python -m pytest`, because "
                "the bare interpreter sees none of the above and importing from the "
                "source tree instead of the venv is the failure this step exists to "
                "prevent. Once both revisions compile, run the developer test; any "
                "compile or test failure replans.")
        reason = "compile deferred to the agent (build-in-loop)"
    else:
        logs = "\n".join((build.get(rev) or {}).get("output", "") or ""
                          for rev in need.get("revisions", []))
        reason = f"{', '.join(need.get('revisions', []))} failed to build"
    verdict = V.Verdict(V.SETUP_ERROR,
                        V.SideReport("failed_to_run", "ERROR", "build_failure"),
                        V.SideReport(), reason=reason)
    action = D.diagnose(verdict, task=task, output=logs[-20000:], context=context,
                        pins=repro_tools.current_pins(ctx, "buggy"))
    ctx.emit("thought",
             ("FIRST PLAN BUILDS BOTH REVISIONS (build-in-loop)" if need.get("deferred")
              else f"FIRST PLAN IS A BUILD REPAIR: {need.get('revisions')} did not build")
             + f" ({need.get('detail', '')[:120]}); diagnosis {action.diagnosis}")
    return action, logs[-20000:]


def drive(ctx, *, client, context: NumericalContext, task: str,
          grading: str = "", max_iterations: int = 7, evidence_budget: int = 2,
          provider: str = "", model: str = "", host: str = "",
          embed_provider: str = "ollama", embed_model: str = "",
          chroma_dir=None, package: PKG.PackageOptions | None = None,
          build_mode: str = "fixed") -> str:
    grading = grading or GRADING.get(task, FULL)
    if ctx.workspace_info is not None:
        ctx.workspace_info["build_mode"] = build_mode
    if task == B.HBEC:
        ctx.rebuild_hint = ("To run this revision on a different Python or with a "
                            "different pinned dependency, plan an action with purpose "
                            "\"rebuild\" and python_version and/or pins (e.g. "
                            "[\"numpy<2.4\"]) instead: the harness rebuilds its venv properly.")
    state = S.RunState(task=task)
    budget = E.EvidenceBudget(max_calls=evidence_budget)
    trace = Trace(task=task)
    diagnosis = None
    extra_evidence = ""
    measured: dict = {}

    from numagent.repro_workspace import ERA_MODE_OFF as _ERA_OFF, era_mode as _era_mode

    if _era_mode() == _ERA_OFF:
        ctx.emit("thought", "ABLATION era-mode=off: no dependency is era-pinned, "
                            "the interpreter is not capped to the commit's epoch, "
                            "and the mined era_constraints are withheld from the "
                            "plan. The environment is whatever pip resolves today.")
    mismatch = _verify_revisions(ctx)
    if mismatch:
        ctx.emit("thought", f"PROVISIONING WARNING (advisory — the run continues): "
                            f"{mismatch}")
        trace.provisioning_warning = mismatch
        if ctx.report is not None:
            ctx.report["provisioning_warning"] = mismatch

    diagnosis, _build_log = _build_repair_diagnosis(ctx, task, context)

    identity = _test_identity(ctx, task)
    import_rule = _import_rule(ctx, task)
    if import_rule:
        ctx.emit("thought", import_rule)
        identity = f"{identity}\n\n{import_rule}" if identity else import_rule
    api_rule = _resolve_api_path(ctx, task, context)
    if api_rule:
        ctx.emit("thought", api_rule)
        identity = f"{identity}\n\n{api_rule}" if identity else api_rule
    verdict = None
    hang = _hang_expected(context)
    if hang:
        ctx.emit("thought", "the report describes a HANG: a timeout on the buggy "
                            "revision counts as the defect, not as a setup error")

    for _ in range(max_iterations):
        plan = _plan_once(client, context, task, state=state, diagnosis=diagnosis,
                          extra_evidence=extra_evidence,
                          grading=("brtg" if task == B.BRTG else grading),
                          test_identity=identity, measurements=measured,
                          ctx=ctx)
        ctx.iterations += 1
        influence = P.evidence_influence(plan, context)
        trace.plans.append(plan.as_dict())
        trace.influence.append(influence)
        ctx.emit("plan", {"iteration": state.iteration + 1,
                          "plan": plan.as_dict(), "influence": influence,
                          "replan_of": diagnosis.diagnosis if diagnosis else "",
                          "evidence_supplied": extra_evidence})
        if plan.error:
            return f"planner failed: {plan.error}"

        output = _run_plan(ctx, plan, trace, task)

        if output.startswith((_REFUSED, _BUILD_FAILED)):
            failed_build = output.startswith(_BUILD_FAILED)
            reason = output[len(_BUILD_FAILED if failed_build else _REFUSED):].strip()
            base = D.POLICY["native_library_failure" if failed_build else "candidate_malformed"]
            action = dataclasses.replace(
                base, instruction=f"{base.instruction}\n\nREASON: {reason}")
            diagnosis = action
            trace.diagnoses.append({"iteration": state.iteration + 1,
                                    "diagnosis": action.diagnosis,
                                    "instruction": reason[:160]})
            ctx.emit("replan", {"after_iteration": state.iteration + 1,
                                "outcome": "NOT_RUN",
                                "diagnosis": action.diagnosis,
                                "instruction": action.instruction,
                                "forbids": list(action.forbids),
                                "evidence_need": "", "measured": {},
                                "moves": D.allowed_moves(task, action)})
            _mark_tokens(trace, client, budget, state.iteration + 1)
            state.record(change=(plan.actions[0].get("step", "") if plan.actions
                                 else ""),
                         outcome="NOT_RUN", diagnosis=action.diagnosis,
                         evidence_used=influence["grounded"], detail=reason[:300])
            state.next_action = action.instruction[:120]
            extra_evidence = ""
            continue

        if task == B.BRTG and not trace.candidate_written:
            action = D.POLICY["no_candidate_written"]
            diagnosis = action
            trace.diagnoses.append({"iteration": state.iteration + 1,
                                    "diagnosis": action.diagnosis,
                                    "instruction": "probe-only iteration: no "
                                                   "T_gen written"})
            ctx.emit("replan", {"after_iteration": state.iteration + 1,
                                "outcome": "NOT_GRADED",
                                "diagnosis": action.diagnosis,
                                "instruction": action.instruction,
                                "forbids": list(action.forbids),
                                "evidence_need": "", "measured": measured,
                                "moves": D.allowed_moves(task, action)})
            state.record(change=(plan.actions[0].get("step", "") if plan.actions
                                 else ""),
                         outcome="NOT_GRADED", diagnosis=action.diagnosis,
                         evidence_used=influence["grounded"],
                         detail=(output or "")[:300])
            state.next_action = action.instruction[:120]
            extra_evidence = ""
            continue

        unbuilt = ((ctx.workspace_info or {}).get("build_mode") == "agent" and not all(
            (((ctx.workspace_info or {}).get("build") or {}).get(r) or {}).get("built")
            for r in ("buggy", "fixed")))
        if task == B.HBEC and not unbuilt:
            _ensure_pyargs_plugin(ctx, "buggy")
            _ensure_pyargs_plugin(ctx, "fixed")
            for helper in ("ensure_reproducing_test_ran",
                           "ensure_test_on_both_revisions"):
                try:
                    getattr(_nag_planning, helper)(ctx)
                except Exception as e:
                    ctx.emit("thought", f"{helper} failed: {type(e).__name__}: {e}")
        else:
            mirrored = _mirror_generated_test(ctx, plan)
            if mirrored:
                ctx.emit("thought", mirrored)

        verdict = V.classify(_test_record(ctx, "buggy"), _test_record(ctx, "fixed"),
                             hang_expected=hang, context=context)
        if unbuilt:
            verdict = V.Verdict(V.SETUP_ERROR,
                                V.SideReport("failed_to_run", "ERROR", "build_failure"),
                                V.SideReport(),
                                reason="a revision has not been built (build-mode agent): "
                                       "no test result counts until both revisions are "
                                       "built through a rebuild action with a recipe")
        ok, note = V.matches_reported(verdict, context)
        if note:
            verdict.reason += f"; {note}"
        if grading == FULL:
            verdict.valid = verdict.valid and ok
        elif not ok:
            ctx.emit("thought", f"NOTE (advisory, not graded): {note}")
        trace.verdicts.append(verdict.as_dict())
        _mark_tokens(trace, client, budget, state.iteration + 1)
        ctx.emit("tool", {"name": "verify_numerical_outcome",
                          "status": verdict.reproduction,
                          "observed": verdict.as_dict()})

        if verdict.reproduction in (V.P2P, V.F2F, V.SETUP_ERROR):
            verdict = _widen_to_other_cases(ctx, verdict, trace, task, hang=hang)

        if verdict.reproduction == V.F2P and verdict.valid:
            trace.final = "F2P" if grading == F2P_ONLY else "VALID_F2P"
            state.record(change=plan.actions[0].get("step", "") if plan.actions else "",
                         outcome=verdict.reproduction, diagnosis="")
            break

        if (grading == F2P_ONLY
                and verdict.buggy.test_outcome == "FAIL"
                and verdict.fixed.test_outcome == "PASS"):
            trace.final = verdict.reproduction
            ctx.emit("thought",
                     f"fail->pass transition observed (verdict "
                     f"{verdict.reproduction}): the developer test already flips, "
                     f"so no replan — stopping.")
            state.record(change=plan.actions[0].get("step", "") if plan.actions else "",
                         outcome=verdict.reproduction, diagnosis="")
            break

        action = D.diagnose(verdict, task=task, output=output, context=context,
                            pins=repro_tools.current_pins(ctx, "buggy"),
                            compile_deferred=_compile_deferred(ctx),
                            package=_package_under_test(ctx))
        if task == B.BRTG and trace.probe_agreement is True \
                and verdict.reproduction in (V.F2F, V.P2P):
            action = D.POLICY["candidate_not_discriminating"]
            ctx.emit("thought", "the two-sided probe measured IDENTICAL output on "
                                "both revisions, so the diagnosis is the INPUT, "
                                "not the oracle")
            trace.probe_agreement = None
        diagnosis = action
        measured = D.measurements(
            verdict, task=task,
            buggy_output=_test_output(ctx, "buggy"),
            fixed_output=_test_output(ctx, "fixed"),
            tolerance_window=trace.tolerance_window)
        if measured:
            ctx.emit("thought", "MEASURED: " + (measured.get("how_to_read_this")
                                                or "values carried into the replan"))
        trace.diagnoses.append({"iteration": state.iteration + 1,
                                "diagnosis": action.diagnosis,
                                "instruction": action.instruction[:160]})
        ctx.emit("replan", {"after_iteration": state.iteration + 1,
                            "outcome": verdict.reproduction,
                            "diagnosis": action.diagnosis,
                            "instruction": action.instruction,
                            "forbids": list(action.forbids),
                            "evidence_need": action.evidence_need,
                            "moves": D.allowed_moves(task, action),
                            "measured": measured})
        state.record(change=(plan.actions[0].get("step", "") if plan.actions else ""),
                     outcome=verdict.reproduction, diagnosis=action.diagnosis,
                     evidence_used=influence["grounded"],
                     detail=S.compact_log(output))
        state.next_action = action.instruction[:120]

        extra_evidence = ""
        if action.evidence_need:
            state.missing_evidence = action.evidence_need
            got = E.retrieve_evidence(context, action.evidence_need, task=task,
                                      budget=budget, provider=provider, model=model,
                                      host=host, embed_provider=embed_provider,
                                      embed_model=embed_model, chroma_dir=chroma_dir)
            if got.get("answer"):
                extra_evidence = got["answer"]
                state.evidence_requests.append({"need": action.evidence_need[:120],
                                                "cached": got.get("cached", False)})
                ctx.emit("thought", f"EVIDENCE[{got.get('question')}] {extra_evidence[:200]}")
            elif got.get("refused"):
                ctx.emit("thought", f"EVIDENCE refused: {got['refused']}")

    trace.evidence = budget.as_dict()
    if task == B.BRTG and not trace.candidate_written and not trace.verdicts:
        trace.final = "NO_CANDIDATE"
        ctx.emit("thought", "NO_CANDIDATE: the iteration cap ran out before a "
                            "generated test was ever written — every plan "
                            "measured and none produced T_gen. Nothing was "
                            "graded; this is not a setup failure.")

    trace.tokens = _token_usage(client, budget)
    state.budget = {"evidence": budget.as_dict()}
    repro_tools.compare_execution_results(ctx)
    repro_tools.finalize_reproduction(ctx)

    if ctx.report is not None:
        ctx.report.setdefault("numeric_pevr", {}).update({
            "task": task, "grading": grading,
            "iterations": state.iteration,
            "replans": max(0, state.iteration - 1),
            "evidence_calls": budget.spent,
            "refusals": len(trace.refusals),
            "tokens_by_iteration": trace.token_marks,
        })

    if package and package.enabled:
        result = PKG.package(ctx, context=context,
                             verdict=(verdict if trace.verdicts else None),
                             task=task, grading=grading, opts=package,
                             hang_timeout=(PKG.HANG_TIMEOUT_S if hang else 0))
        ctx.history.append({"numbugbench_package": result.as_dict()})
        if ctx.report is not None:
            ctx.report["numbugbench_package"] = result.as_dict()
        if result.packaged:
            ctx.emit("tool", {"name": "package_numbugbench_instance",
                              "image": result.image,
                              "artifacts": result.artifacts,
                              "instance": result.metadata.get("instance_id")})
        else:
            ctx.emit("thought", f"not packaged: {result.reason}")

    ctx.history.append({"numeric_pevr": trace.as_dict()})
    ctx.history.append({"numeric_pevr_state": state.as_dict()})
    if ctx.report is not None:
        ctx.report["numeric_pevr"] = {
            **(ctx.report.get("numeric_pevr") or {}),
            "task": task,
            "grading": grading,
            "final": trace.final or (trace.verdicts[-1]["reproduction"]
                                     if trace.verdicts else "no_verdict"),
            "iterations": state.iteration,
            "evidence_calls": budget.spent,
            "replans": max(0, state.iteration - 1),
            "tokens": trace.tokens,
            "refusals": len(trace.refusals),
            "verdicts": [v["reproduction"] for v in trace.verdicts],
            "diagnoses": [d["diagnosis"] for d in trace.diagnoses],
            "evidence_influence": trace.influence,
        }
    return ""


def reproduce(spec, *, context_file, task: str = B.BRTG, grading: str = "",
              build_mode: str = "fixed",
              client=None, package: PKG.PackageOptions | None = None,
              prepared_image: str = "",
              provider: str = "openai", model: str = "", work_dir,
              token: str | None = None, max_iterations: int = 7,
              evidence_budget: int = 2, cmd_timeout_s: float = 1800.0,
              keep_container: bool = False, stream_output: bool = False,
              host: str = "", embed_provider: str = "ollama", embed_model: str = "",
              chroma_dir=None, on_event=None):
    defer_build = build_mode in ("in-loop", "agent")
    if defer_build:
        task = B.HBEC
        grading = F2P_ONLY
    grading = grading or GRADING.get(task, FULL)
    client = client or build_client(provider, model)
    context = NumericalContext.from_dict(
        json.loads(Path(context_file).read_text(encoding="utf-8")))
    from numagent.repro_workspace import ERA_MODE_OFF, era_mode

    era_withheld = era_mode() == ERA_MODE_OFF and bool(
        (context.environment or {}).get("era_constraints"))
    if era_withheld:
        context.environment["era_constraints"] = []
    from numagent.repro_specs import trigger_python
    wanted = trigger_python((context.numerical_failure or {}).get("trigger_conditions"))
    if wanted and wanted != spec.python_version:
        spec.environment_notes = tuple(spec.environment_notes or ()) + (
            f"Python {wanted}, not {spec.python_version}: the bug's trigger "
            f"conditions require it.",)
        spec.python_version = wanted
    return run_session(
        spec, reasoning=NAME, work_dir=work_dir, token=token,
        cmd_timeout_s=cmd_timeout_s, keep_container=keep_container,
        stream_output=stream_output, on_event=on_event,
        probes=(grading == FULL),
        prepared_image=prepared_image,
        build_repair=(task == B.HBEC),
        defer_build=defer_build,
        driver=lambda ctx: drive(ctx, client=client, context=context, task=task,
                                 grading=grading, package=package,
                                 max_iterations=max_iterations,
                                 evidence_budget=evidence_budget,
                                 provider=provider, model=model, host=host,
                                 embed_provider=embed_provider,
                                 embed_model=embed_model, chroma_dir=chroma_dir,
                                 build_mode=build_mode))

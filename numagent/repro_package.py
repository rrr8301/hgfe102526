from __future__ import annotations

import base64
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from .repro_container import PROBE_MARKER
from .repro_workspace import BUGGY_DIR, FIXED_DIR

BENCH_DIR = "/benchmark"

#: Where a revision's own probes live INSIDE that revision. A probe only means
#: anything next to the code it measures — it is run with the revision as the
#: working directory, and that is what makes `import <package>` resolve to THIS
#: revision — so the image keeps each revision's probes in the revision itself:
#: /workspace/buggy/<PROBES_IN_REVISION>/<label>/. /benchmark keeps the same
#: files as the index a reader browses; the two copies are identical.
PROBES_IN_REVISION = "numagent_probes"

#: statuses worth packaging by default. Only a full reproduction ships: a
#: PARTIALLY_REPRODUCED run left something unobserved or still flagged the fixed
#: revision, so its image would carry an experiment its own harness could not
#: confirm. `--package-any-status` widens this to every status, including
#: PARTIALLY_REPRODUCED.
DEFAULT_STATUSES = ("REPRODUCED",)

_EXT = {"python": "py", "javascript": "js", "typescript": "ts", "go": "go",
        "rust": "rs", "java": "java", "c": "c", "cpp": "cpp"}


@dataclass
class PackageOptions:
    """How (and whether) to package a reproduction as a benchmark image."""
    enabled: bool = False
    tag: str = "latest"
    prefix: str = ""                       # e.g. "numbench/" or "ghcr.io/me/"
    statuses: tuple = DEFAULT_STATUSES     # empty tuple = package any status
    # package at the end of the session when the agent never asked. Turning it
    # off leaves packaging entirely to the agent's own judgement.
    fallback: bool = True

    def wants(self, status: str) -> bool:
        return bool(self.enabled) and (not self.statuses or status in self.statuses)


# --------------------------------------------------------------------------- #
# naming
# --------------------------------------------------------------------------- #
def _docker_safe(text: str) -> str:
    """Docker repository names are lowercase [a-z0-9._-]."""
    out = re.sub(r"[^a-z0-9._-]+", "-", (text or "").lower()).strip("-._")
    return out or "repo"


def issue_id(spec) -> str:
    """The issue/PR identifier: the trailing number of the slug (``sympy_29798``
    → ``29798``), else the slug's last segment."""
    m = re.search(r"(\d+)\s*$", spec.slug or "")
    if m:
        return m.group(1)
    tail = (spec.slug or "").replace("/", "_").split("_")[-1]
    return _docker_safe(tail) or "0"


def image_name(spec, options: PackageOptions | None = None) -> str:
    """``<repo>-<issue_id>:<tag>`` — the benchmark instance's image."""
    options = options or PackageOptions()
    repo = _docker_safe((spec.repo or spec.slug or "repo").split("/")[-1])
    prefix = options.prefix
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return f"{prefix}{repo}-{issue_id(spec)}:{options.tag or 'latest'}"


def _probe_dir_name(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label or "probe").strip("_") or "probe"


# --------------------------------------------------------------------------- #
# the replay scripts
# --------------------------------------------------------------------------- #
_SH_HEADER = "#!/usr/bin/env bash\n"


def _b64_block(text: str) -> str:
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76))


#: The PATH the live harness gives every command it runs in a revision
#: (`Container.run`'s prelude). A revision that had to be BUILT owns a
#: virtualenv holding its own build of the package, and putting it first is what
#: makes `python`, `pip` and `pytest` mean THIS revision's. The packaged scripts
#: ran the bare command without it, so inside the image `python` was the base
#: interpreter and every probe died on "No module named 'scipy'" — on both
#: revisions, which reads as the instance being broken rather than as the image
#: missing one line of setup.
_VENV_PRELUDE = (
    'if [ -x "$PWD/.venv/bin/python" ]; then PATH="$PWD/.venv/bin:$PATH"; fi\n'
    'if [ -f "$PWD/.numagent-constraints.txt" ]; then '
    'export PIP_CONSTRAINT="$PWD/.numagent-constraints.txt"; fi\n'
    'export PATH\n')


def _render_probe_run_sh(script: dict) -> str:
    """A standalone replay of one probe: write the harness files back, then run
    the exact command the reproducing session ran, from the same directory."""
    plan = script.get("plan") or {}
    workdir = plan.get("workdir") or script.get("working_dir") or BUGGY_DIR
    lines = [
        _SH_HEADER,
        f"# numagent benchmark probe {script.get('label')!r} on the "
        f"{script.get('revision')} revision ({script.get('language')}).\n",
        f"# The values it observed are the JSON object printed after {PROBE_MARKER}.\n",
        "set -u\n",
        'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n',
        f"WORKDIR={shlex.quote(workdir)}\n",
        '_write() { mkdir -p "$(dirname "$1")"; base64 -d > "$1"; }\n',
    ]
    for d in plan.get("dirs") or []:
        lines.append(f"mkdir -p {shlex.quote(d)}\n")
    for path, text in (plan.get("files") or {}).items():
        lines.append(f"_write {shlex.quote(path)} <<'__NUMAGENT_B64__'\n"
                     f"{_b64_block(text)}\n__NUMAGENT_B64__\n")
    lines.append('cd "$WORKDIR" || exit 1\n')
    # the venv prelude lives INSIDE command.sh: `bash -l` sources /etc/profile,
    # which rewrites PATH, so anything exported out here is gone by the time the
    # command runs
    lines.append('exec bash -l "$HERE/command.sh"\n')
    return "".join(lines)


_RUN_PROBE_SH = _SH_HEADER + """# Run one packaged probe: run_probe.sh <buggy|fixed> [label]
# With no label, every probe packaged for that revision runs.
set -u
BENCH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REV="${1:-buggy}"
LABEL="${2:-}"
case "$REV" in buggy|fixed) ;; *) echo "usage: $0 <buggy|fixed> [label]" >&2; exit 2;; esac

run_one() {
  local dir="${1%/}"
  if [ ! -f "$dir/run.sh" ]; then
    echo "no replayable probe in $dir" >&2
    return 2
  fi
  echo "=== probe '$(basename "$dir")' on $REV ==="
  bash "$dir/run.sh"
  local rc=$?
  echo "=== exit $rc ==="
  return $rc
}

if [ -n "$LABEL" ]; then
  run_one "$BENCH/probes/$REV/$LABEL"
  exit $?
fi
rc=0
for dir in "$BENCH/probes/$REV"/*/; do
  [ -d "$dir" ] || continue
  run_one "$dir" || rc=$?
done
exit $rc
"""


def _render_run_tests_sh(tests: dict) -> str:
    """Rerun the reproducing test on either revision, with the command the agent
    actually used — it knows this repo's build layout; a generic guess does not."""
    if not tests:
        return (_SH_HEADER + "# No reproducing-test command was recorded for this instance.\n"
                "echo 'no test command was recorded during reproduction' >&2\nexit 2\n")
    body = [_SH_HEADER,
            "# Run the reproducing test: run_tests.sh [buggy|fixed|both]\n",
            "set -u\n",
            'REV="${1:-both}"\n\n']
    for rev, cmd in sorted(tests.items()):
        workdir = BUGGY_DIR if rev == "buggy" else FIXED_DIR
        body.append(f'run_{rev}() {{\n'
                    f'  echo "=== reproducing test on {rev} ==="\n'
                    f'  cd {shlex.quote(workdir)} || return 1\n'
                    f'  bash -lc {shlex.quote(_VENV_PRELUDE + cmd)}\n'
                    f'  echo "=== exit $? ==="\n'
                    f'}}\n\n')
    both = "; ".join(f"run_{rev}" for rev in sorted(tests))
    body.append('case "$REV" in\n')
    for rev in sorted(tests):
        body.append(f"  {rev}) run_{rev} ;;\n")
    body.append(f"  both) {both} ;;\n")
    body.append(f'  *) echo "usage: $0 [{"|".join(sorted(tests))}|both]" >&2; exit 2 ;;\n')
    body.append("esac\n")
    return "".join(body)


def _render_compare_sh(header: str) -> str:
    """The image's default command: every packaged probe on both revisions, then
    the reproducing test on both — the buggy-vs-fixed comparison, in one run."""
    return _SH_HEADER + f"""# Buggy vs fixed, in one command (this image's default CMD).
# Each packaged probe runs on BOTH revisions: the defect it exposes on buggy
# should be absent from the same probe's output on fixed.
set -u
BENCH="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"

cat <<'__HEADER__'
{header}
__HEADER__

labels() {{
  for dir in "$BENCH"/probes/*/*/; do
    [ -d "$dir" ] || continue
    basename "${{dir%/}}"
  done | sort -u
}}

rc=0
for label in $(labels); do
  echo "############ probe: $label ############"
  for rev in buggy fixed; do
    if [ -f "$BENCH/probes/$rev/$label/run.sh" ]; then
      bash "$BENCH/run_probe.sh" "$rev" "$label" || rc=$?
    else
      echo "=== probe '$label' on $rev: not packaged (never run on that revision) ==="
    fi
    echo
  done
done

if [ -f "$BENCH/run_tests.sh" ]; then
  echo "############ reproducing test ############"
  bash "$BENCH/run_tests.sh" both || true
fi
exit $rc
"""


_README = """# {slug} — numerical-bug benchmark instance

`{repo}` issue **{issue}** — packaged by numagent after an agentic reproduction
finished with status **{status}** ({transition}).

    /workspace/buggy    {buggy_commit}  (+ the reproducing-test patch)
    /workspace/buggy/numagent_probes/<label>/   the probes for THIS revision
    /workspace/fixed    {fixed_commit}
    /workspace/fixed/numagent_probes/<label>/   the probes for THIS revision
    /benchmark          this directory

Both trees are exactly as the reproducing session left them, dependencies
installed and built, so nothing here needs a network.

## Buggy vs fixed

    docker run --rm {image}                    # every probe on both revisions
    /benchmark/run_probe.sh buggy {first_label}
    /benchmark/run_probe.sh fixed {first_label}
    /benchmark/run_tests.sh both

A probe prints its observed values as a JSON object after the line
`{marker}`: recorded values, iteration trace, warnings and IEEE
FP-exception state. On `buggy` that payload carries the reported defect; on
`fixed` the same probe shows the corrected behavior.
`/benchmark/probes/<rev>/<label>/observation.json` holds what each probe
produced during the original reproduction, to compare a replay against.

## Files

- `metadata.json` — commits, test selector, per-probe verdicts, the oracle's
  comparison, the reproduction report.
- `examples.json` — each input/output example the report named, the value the
  buggy revision produced, and the value the fixed one produced.
- `patches/source.patch` — the fix (already applied in `/workspace/fixed`).
- `patches/test.patch` — the reproducing test (already applied in
  `/workspace/buggy`).
- `probes/<rev>/<label>/probe.<ext>` — the issue-specific probe source. The same
  files sit inside the revision they measure, at
  `/workspace/<rev>/numagent_probes/<label>/`, which is where a probe is meant
  to be read: beside the code whose behaviour it records. Either copy runs.
- `build/<rev>.log` — how each revision was built: the compiler and pip output
  the reproducing session produced. What the image runs is what this log built,
  so a probe that behaves differently here than it did then is diagnosable
  without rebuilding anything.
"""


# --------------------------------------------------------------------------- #
# staging
# --------------------------------------------------------------------------- #
def _copy_patch(src: str, dest: Path) -> str:
    p = Path(src) if src else None
    if not p or not p.is_file():
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    return str(dest.relative_to(dest.parent.parent))


def _test_commands(ctx) -> dict:
    out = {}
    for rev in ("buggy", "fixed"):
        rec = (ctx.results.get(rev) or {}).get("test")
        if rec and rec.get("command"):
            out[rev] = rec["command"]
    return out


def _revision_commands(ctx, rev: str) -> list:
    """The setup/build commands that made this revision runnable — the recipe the
    image already has applied, kept so it can be audited or redone."""
    return [{"purpose": c.get("purpose"), "command": c.get("command"),
             "exit_code": c.get("exit_code")}
            for c in (ctx.results.get(rev) or {}).get("commands") or []
            if c.get("purpose") in ("setup", "build")]


def build_stage(ctx, stage: Path, *, image: str, report: dict,
                notes: str = "") -> dict:
    """Lay out the `/benchmark` tree on the host. Returns its metadata."""
    stage = Path(stage)
    (stage / "patches").mkdir(parents=True, exist_ok=True)
    spec = ctx.spec

    patches = {
        "source": _copy_patch(spec.source_patch_path,
                              stage / "patches" / f"{spec.slug}.source.patch"),
        "test": _copy_patch(spec.test_patch_path,
                            stage / "patches" / f"{spec.slug}.test.patch"),
    }

    # --- probes: one replayable script per (revision, label) ----------------
    probe_meta: dict = {}
    first_label = ""
    for rev in ("buggy", "fixed"):
        for label, script in sorted((ctx.probe_scripts.get(rev) or {}).items()):
            name = _probe_dir_name(label)
            first_label = first_label or name
            pdir = stage / "probes" / rev / name
            pdir.mkdir(parents=True, exist_ok=True)
            ext = _EXT.get(script.get("language") or "", "txt")
            (pdir / f"probe.{ext}").write_text(script.get("code") or "", encoding="utf-8")
            plan = script.get("plan") or {}
            replayable = bool(plan.get("command"))
            if replayable:
                (pdir / "command.sh").write_text(
                    _VENV_PRELUDE + plan["command"] + "\n", encoding="utf-8")
                (pdir / "run.sh").write_text(_render_probe_run_sh(script), encoding="utf-8")
                (pdir / "run.sh").chmod(0o755)
            obs = ((ctx.results.get(rev) or {}).get("probes") or {}).get(label)
            if obs is not None:
                (pdir / "observation.json").write_text(
                    json.dumps(obs, indent=2, default=str), encoding="utf-8")
            entry = probe_meta.setdefault(name, {"label": label, "revisions": {}})
            entry["language"] = script.get("language")
            entry["expected"] = script.get("expected")
            entry["tolerances"] = {"rtol": script.get("rtol"), "atol": script.get("atol"),
                                   "residual_tol": script.get("residual_tol")}
            entry["revisions"][rev] = {
                "path": f"probes/{rev}/{name}",
                "replayable": replayable,
                "status": script.get("status"),
                "pathology": script.get("pathology"),
                "working_dir": script.get("working_dir"),
            }
    # the oracle's per-label verdict, so the image says what each probe proved
    for pair in ((ctx.comparison or {}).get("numeric") or {}).get("pairs") or []:
        entry = probe_meta.get(_probe_dir_name(pair.get("label", "")))
        if entry is not None:
            entry["outcome"] = pair.get("outcome")

    # --- the reported values, and what each revision actually produced ------
    examples = (ctx.comparison or {}).get("examples") or {}
    if examples.get("n_examples"):
        (stage / "examples.json").write_text(
            json.dumps(examples, indent=2, default=str), encoding="utf-8")

    # --- build logs ---------------------------------------------------------
    # The image IS the built revisions, so how they were built is part of the
    # instance: an era-pinned numpy, a compiler flag that had to be relaxed, a
    # build requirement the harness supplied. The revision's own
    # `.numagent-build.log` is the whole log; the result's excerpt (first error
    # blocks + tail) is the fallback when the container no longer has the file.
    builds = (ctx.workspace_info or {}).get("build") or {}
    build_logs = {}
    for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
        info = builds.get(rev)
        if not isinstance(info, dict):
            continue
        text = ""
        container = getattr(ctx, "container", None)
        if container is not None:
            try:
                got = container.run("cat .numagent-build.log 2>/dev/null",
                                    workdir=workdir, timeout_s=120)
                text = got.stdout or ""
            except Exception:                            # noqa: BLE001
                text = ""
        if not text.strip():
            text = str(info.get("output") or "")
        if not text.strip():
            continue
        (stage / "build").mkdir(exist_ok=True)
        (stage / "build" / f"{rev}.log").write_text(text, encoding="utf-8")
        build_logs[rev] = {
            "path": f"build/{rev}.log",
            "built": info.get("built"),
            "exit_code": info.get("exit_code"),
            "imports_from": info.get("imports_from"),
            "era_pins": info.get("era_pins"),
            "build_requires": info.get("build_requires"),
            "remedies": info.get("remedies"),
            "complete": bool(text.strip()) and "…" not in text[:200],
        }

    # --- runner scripts -----------------------------------------------------
    tests = _test_commands(ctx)
    header = (f"{spec.slug}  —  {spec.repo} issue {issue_id(spec)}  "
              f"[{report.get('status', '?')}]\n"
              f"buggy  {BUGGY_DIR}  {spec.buggy_commit}\n"
              f"fixed  {FIXED_DIR}  {spec.fixed_commit}")
    for name, text in (("run_probe.sh", _RUN_PROBE_SH),
                       ("run_tests.sh", _render_run_tests_sh(tests)),
                       ("compare.sh", _render_compare_sh(header))):
        f = stage / name
        f.write_text(text, encoding="utf-8")
        f.chmod(0o755)

    ws = ctx.workspace_info or {}
    meta = {
        "created_by": "numagent",
        "image": image,
        "slug": spec.slug,
        "repo": spec.repo,
        "issue_id": issue_id(spec),
        "bug_type": spec.bug_type,
        "status": report.get("status", ""),
        "transition": report.get("transition", ""),
        "fail_to_pass": report.get("fail_to_pass"),
        "numeric_verdict": report.get("numeric_verdict"),
        "examples": {k: examples.get(k) for k in
                     ("verdict", "n_examples", "n_judged", "n_reproduced", "n_contested")},
        "rationale": report.get("rationale", ""),
        # the agent's own account of what this instance demonstrates
        "notes": notes,
        "test_selector": spec.test_selector,
        "revisions": {
            "buggy": {"dir": BUGGY_DIR, "commit": spec.buggy_commit,
                      "test_patch_applied": ws.get("test_patch_applied"),
                      "test_command": tests.get("buggy", ""),
                      "commands": _revision_commands(ctx, "buggy")},
            "fixed": {"dir": FIXED_DIR, "commit": spec.fixed_commit,
                      "test_command": tests.get("fixed", ""),
                      "commands": _revision_commands(ctx, "fixed")},
        },
        "patches": patches,
        "build": build_logs,
        "probes": list(probe_meta.values()),
        "comparison": ctx.comparison or {},
        "report": report,
        "workspace": ws,
        "probes_in_revision": {
            "buggy": f"{BUGGY_DIR}/{PROBES_IN_REVISION}",
            "fixed": f"{FIXED_DIR}/{PROBES_IN_REVISION}"},
        "entrypoints": {"compare": f"{BENCH_DIR}/compare.sh",
                        "probe": f"{BENCH_DIR}/run_probe.sh <buggy|fixed> [label]",
                        "test": f"{BENCH_DIR}/run_tests.sh [buggy|fixed|both]"},
    }
    (stage / "metadata.json").write_text(json.dumps(meta, indent=2, default=str),
                                         encoding="utf-8")
    (stage / "README.md").write_text(_README.format(
        slug=spec.slug, repo=spec.repo, issue=issue_id(spec),
        status=meta["status"] or "?", transition=meta["transition"] or "?",
        buggy_commit=spec.buggy_commit or "?", fixed_commit=spec.fixed_commit or "?",
        image=image, first_label=first_label or "<label>", marker=PROBE_MARKER),
        encoding="utf-8")
    return meta


# --------------------------------------------------------------------------- #
# packaging
# --------------------------------------------------------------------------- #
def _commit_changes(meta: dict) -> list:
    lab = lambda k, v: f'LABEL numagent.{k}="{str(v).replace(chr(34), "")}"'  # noqa: E731
    return [
        f'CMD ["bash", "{BENCH_DIR}/compare.sh"]',
        "WORKDIR /workspace",
        lab("slug", meta["slug"]), lab("repo", meta["repo"]),
        lab("issue", meta["issue_id"]), lab("bug_type", meta["bug_type"]),
        lab("buggy_commit", meta["revisions"]["buggy"]["commit"]),
        lab("fixed_commit", meta["revisions"]["fixed"]["commit"]),
        lab("status", meta["status"]),
        lab("numeric_verdict", meta["numeric_verdict"] or ""),
    ]


def package_reproduction(ctx, *, options: PackageOptions | None = None,
                         report: dict | None = None, notes: str = "") -> dict:
    """Snapshot the finished reproduction as ``<repo>-<issue_id>``.

    Runs against the still-live container, so it must happen before teardown.
    Returns ``{"packaged": bool, "image": …, "probes": n, "error": …}`` — a
    packaging failure is reported, never raised: the reproduction itself already
    succeeded and its verdict must not depend on Docker's willingness to commit.
    """
    options = options or PackageOptions()
    report = report if report is not None else (ctx.report or {})
    status = report.get("status", "")
    image = image_name(ctx.spec, options)
    info = {"packaged": False, "image": image, "status": status,
            "n_probes": 0, "error": ""}

    if not options.wants(status):
        info["error"] = (f"status {status!r} not in {options.statuses}"
                         if options.enabled else "packaging not requested")
        return info
    if not getattr(ctx.container, "started", False):
        info["error"] = "container is no longer running"
        return info

    try:
        with TemporaryDirectory(prefix="numagent_bench_") as tmp:
            stage = Path(tmp) / "benchmark"
            stage.mkdir(parents=True)
            meta = build_stage(ctx, stage, image=image, report=report, notes=notes)
            ctx.container.copy_in(stage, BENCH_DIR)
            # and each revision's probes into the revision itself
            for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
                probes = stage / "probes" / rev
                if probes.is_dir() and any(probes.iterdir()):
                    ctx.container.copy_in(probes, f"{workdir}/{PROBES_IN_REVISION}")
                    info.setdefault("probes_in_revision", {})[rev] = \
                        f"{workdir}/{PROBES_IN_REVISION}"
            res = ctx.container.commit(image, _commit_changes(meta))
        if res.exit_code != 0:
            info["error"] = f"docker commit failed: {(res.stderr or '').strip()[:300]}"
            return info
        info.update({"packaged": True, "n_probes": len(meta["probes"]),
                     "image_id": (res.stdout or "").strip()[:71],
                     "benchmark_dir": BENCH_DIR})
    except Exception as e:  # noqa: BLE001 — packaging must not sink the result
        info["error"] = f"{type(e).__name__}: {e}"[:300]
    return info


# --------------------------------------------------------------------------- #
# replay: package a reproduction that has already finished
# --------------------------------------------------------------------------- #
#: `package_reproduction` runs against the STILL-LIVE container, so a run that
#: finished without `--package-image` cannot be packaged afterwards — the
#: container is gone. Everything the image needs, though, is already in the
#: run's result JSON: every probe's source, the comparison, the report, and the
#: spec says how to rebuild both revisions. So the image can be rebuilt from
#: that file, deterministically and with no model in the loop: provision a fresh
#: container from the spec, replay each probe to confirm it still runs, and
#: write the same `/benchmark` tree.


class _ReplayContext:
    """The handful of attributes :func:`build_stage` reads, filled from a result.

    Not a RunContext: nothing here executes the agent, and the fields a live run
    fills by acting (history, iterations, packaging policy) have no meaning for
    a replay.
    """

    def __init__(self, spec, container, result: dict):
        self.spec = spec
        self.container = container
        self.comparison = result.get("comparison") or {}
        self.workspace_info = result.get("workspace") or {}
        self.results = {"buggy": {"probes": {}, "commands": []},
                        "fixed": {"probes": {}, "commands": []}}
        self.probe_scripts = {"buggy": {}, "fixed": {}}
        self.packages = {}
        self._load_probes(result)
        self._load_tests(result)

    # the run recorded each probe's language and outcome in its history; the
    # command that ran it is regenerated from the source, which is deterministic
    def _load_probes(self, result: dict) -> None:
        from .repro_container import PROBE_MARKER
        from .repro_probe_langs import plan_probe

        meta = {}
        for entry in result.get("history") or []:
            if entry.get("tool") == "run_numerical_probe" and entry.get("label"):
                meta[(entry.get("revision"), entry["label"])] = entry
        evidence_probes = {
            (p.get("label")): p for p in
            ((self.comparison.get("numeric") or {}).get("pairs") or [])}
        for rev, workdir in (("buggy", BUGGY_DIR), ("fixed", FIXED_DIR)):
            for label, code in ((result.get("probe_code") or {}).get(rev) or {}).items():
                if not str(code or "").strip():
                    continue
                info = meta.get((rev, label), {})
                language = info.get("language") or "python"
                try:
                    plan = plan_probe(language, code, marker=PROBE_MARKER,
                                      workdir=workdir)
                    plan_dict = {"command": plan.command,
                                 "files": getattr(plan, "files", None)}
                except Exception:                       # noqa: BLE001
                    plan_dict = {}                      # packaged, not replayable
                self.probe_scripts[rev][label] = {
                    "label": label, "revision": rev, "working_dir": workdir,
                    "language": language, "code": code, "extra_flags": "",
                    "expected": info.get("expected"), "rtol": None, "atol": None,
                    "residual_tol": None, "timeout": 300,
                    "status": info.get("status"), "pathology": info.get("pathology"),
                    "plan": plan_dict,
                }
                pair = evidence_probes.get(label) or {}
                observed = pair.get(f"{rev}_values")
                if observed is not None:
                    self.results[rev]["probes"][label] = {
                        "label": label, "revision": rev,
                        "status": info.get("status"),
                        "observed_records": observed,
                        "pathology": {"present": info.get("pathology"),
                                      "reasons": (pair.get(rev) or {}).get("reasons") or []},
                        "replayed_from": "result JSON",
                    }

    def _load_tests(self, result: dict) -> None:
        evidence = ((result.get("report") or {}).get("evidence") or {}).get("test") or {}
        for rev in ("buggy", "fixed"):
            command = (evidence.get(rev) or {}).get("command") or ""
            if not command:
                # the test command is not always kept on the evidence; the run's
                # own history is the fallback, newest first
                for entry in reversed(result.get("history") or []):
                    if entry.get("revision") == rev and "pytest" in str(entry.get("command", "")):
                        command = entry["command"]
                        break
            if command:
                self.results[rev]["test"] = {"command": command}

    def emit(self, *args, **kwargs) -> None:            # a replay says nothing
        return None

    def store(self, revision: str) -> dict:
        return self.results.setdefault(revision, {"probes": {}, "commands": []})

    def scripts(self, revision: str) -> dict:
        return self.probe_scripts.setdefault(revision, {})

    def package_of(self, working_dir: str) -> tuple:
        return ("", "")


def package_from_result(result: dict, spec, container, *,
                        options: PackageOptions | None = None,
                        notes: str = "") -> dict:
    """Rebuild the benchmark image for a run that has already finished.

    `container` must be started and provisioned (both revisions present) — the
    caller does that with the same `prepare_workspace` a live run uses, so the
    image is built from the revisions themselves rather than from a description
    of them.
    """
    options = options or PackageOptions(enabled=True)
    ctx = _ReplayContext(spec, container, result)
    report = result.get("report") or {"status": result.get("status", "")}
    info = package_reproduction(ctx, options=options, report=report,
                                notes=notes or "packaged from a stored result")
    info["replayed"] = True
    info["n_probe_sources"] = sum(len(v) for v in ctx.probe_scripts.values())
    return info

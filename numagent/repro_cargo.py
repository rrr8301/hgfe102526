from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .repro_container import Container, write_file

#: resolution died, as opposed to compilation — a yanked or vanished crate
_RESOLUTION_ERROR = re.compile(
    r"failed to select a version|is yanked|no matching package(?: named)?"
    r"|could not find .* in registry", re.I)

#: ``failed to select a version for the requirement `wgpu = "^0.18.0"``` — the
#: requirement Cargo could not satisfy, which is the thing to move.
_UNSATISFIED = re.compile(r"""requirement\s+`([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"`""")

#: ``required by package `burn-wgpu v0.12.0 (/workspace/buggy/burn-wgpu)``
_BLAMED = re.compile(r"required by package `[^`]*?\((/[^)`]+)\)`")

_CRATES_IO = "https://crates.io/api/v1/crates/{name}/versions"
_UA = "numagent-benchmark (numerical-bug reproduction harness)"


def _semver(num: str):
    """(major, minor, patch) for ordering — pre-release versions are skipped."""
    core = num.split("+")[0]
    if "-" in core:
        return None
    parts = core.split(".")
    try:
        return tuple(int(p) for p in (parts + ["0", "0"])[:3])
    except ValueError:
        return None


def available_versions(name: str, timeout: float = 20.0) -> list:
    """Non-yanked releases of `name`, oldest first. Empty when crates.io is unreachable."""
    req = urllib.request.Request(_CRATES_IO.format(name=name), headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return []
    live = [(v_, v["num"]) for v in payload.get("versions") or []
            if not v.get("yanked") and (v_ := _semver(v.get("num") or ""))]
    return [num for _, num in sorted(live)]


def nearest_version(name: str, requirement: str) -> str:
    """The published version closest to a requirement nothing satisfies any more.

    Upwards first: the smallest release above what was asked for is the least
    the instance can be moved and still resolve. Only if the crate has nothing
    newer does it fall back to the newest release below.
    """
    wanted = _semver(requirement.lstrip("^~=><! ").split(",")[0].strip()) or (0, 0, 0)
    versions = available_versions(name)
    above = [v for v in versions if (_semver(v) or (0, 0, 0)) > wanted]
    below = [v for v in versions if (_semver(v) or (0, 0, 0)) < wanted]
    return above[0] if above else (below[-1] if below else "")


# --------------------------------------------------------------------------- #
# manifest editing
# --------------------------------------------------------------------------- #
def _dependency_line(text: str, name: str):
    """Index of the line declaring `name` as a dependency, or None."""
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    for i, line in enumerate(text.splitlines()):
        if pattern.match(line):
            return i
    return None


def retarget_dependency(text: str, name: str, version: str) -> str:
    """Point `name`'s version requirement at `version`, in either declaration form.

    ``wgpu = "0.18.0"`` and ``wgpu = { version = "0.18.0", features = [...] }``
    are the same statement written two ways; everything else on the line — the
    features, the flags — is left exactly as the project wrote it.
    """
    lines = text.splitlines()
    i = _dependency_line(text, name)
    if i is None:
        return text
    line = lines[i]
    if re.search(r"version\s*=\s*\"[^\"]*\"", line):
        lines[i] = re.sub(r"(version\s*=\s*)\"[^\"]*\"", rf'\1"{version}"', line, count=1)
    else:
        lines[i] = re.sub(r"(=\s*)\"[^\"]*\"", rf'\1"{version}"', line, count=1)
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _read(container: Container, path: str) -> str:
    return container.run(f"cat {path}", workdir="/", timeout_s=60).stdout or ""


def _declaring_manifest(container: Container, workdir: str, name: str,
                        blamed: list) -> str:
    """Which manifest states the dead requirement: the workspace root, or a member.

    Cargo blames the package that USES the dependency, but burn — like most
    workspaces — declares versions once in the root's ``[workspace.dependencies]``
    and inherits them, so the root is checked first.
    """
    root = f"{workdir}/Cargo.toml"
    text = _read(container, root)
    head = text.find("[workspace.dependencies]")
    if head >= 0:
        table = text[head:]
        end = table.find("\n[", 1)          # the table ends at the next one
        if _dependency_line(table[:end if end > 0 else len(table)], name) is not None:
            return root
    for path in blamed:
        manifest = f"{path.rstrip('/')}/Cargo.toml"
        if _dependency_line(_read(container, manifest), name) is not None:
            return manifest
    return ""


# --------------------------------------------------------------------------- #
# the repair
# --------------------------------------------------------------------------- #
def resolves(container: Container, workdir: str, timeout_s: float = 900.0):
    """(ok, output) for Cargo's resolution of the workspace at `workdir`."""
    r = container.run("cargo metadata --format-version 1 -q >/dev/null",
                      workdir=workdir, timeout_s=timeout_s)
    return r.exit_code == 0, ((r.stderr or "") + "\n" + (r.stdout or ""))


def apply_retargets(container: Container, workdir: str, retargets) -> list:
    """Apply `{crate, to, manifest}` edits to one revision; return those that took."""
    done = []
    for edit in retargets:
        manifest = f"{workdir}/{edit['manifest']}".replace("//", "/")
        text = _read(container, manifest)
        moved = retarget_dependency(text, edit["crate"], edit["to"])
        if moved != text:
            write_file(container, manifest, moved)
            done.append(edit)
    return done


def repair_cargo_workspace(container: Container, workdir: str, *, keep=(),
                           max_rounds: int = 6, timeout_s: float = 900.0) -> dict:
    """Move dead requirements until the workspace resolves, or explain why not.

    `keep` names the crates the instance itself lives in. Rewriting a manifest
    there would change what the reproduction actually compiles, so the repair
    stops and reports instead.
    """
    info = {"applied": False, "retargets": [], "resolves": None, "reason": ""}
    present = container.run(f"test -f {workdir}/Cargo.toml && echo yes",
                            workdir="/", timeout_s=60)
    if "yes" not in (present.stdout or ""):
        info["reason"] = "not a Cargo workspace"
        return info

    protected = {str(k).strip("/ ") for k in keep}
    for _ in range(max_rounds):
        ok, output = resolves(container, workdir, timeout_s)
        if ok:
            info["resolves"] = True
            return info
        if not _RESOLUTION_ERROR.search(output):
            info.update(resolves=False,
                        reason="cargo failed for a reason other than resolution")
            return info
        unsatisfied = _UNSATISFIED.search(output)
        if not unsatisfied:
            info.update(resolves=False,
                        reason="cargo did not name the requirement it cannot satisfy")
            return info
        name, requirement = unsatisfied.group(1), unsatisfied.group(2)
        if any(e["crate"] == name for e in info["retargets"]):
            info.update(resolves=False,
                        reason=f"`{name}` still does not resolve after retargeting it")
            return info

        blamed = [p for p in _BLAMED.findall(output) if p.startswith(workdir)]
        manifest = _declaring_manifest(container, workdir, name, blamed)
        if not manifest:
            info.update(resolves=False,
                        reason=f"could not find where `{name}` is declared")
            return info
        relative = manifest[len(workdir):].strip("/")   # "Cargo.toml" | "crate/Cargo.toml"
        member = relative[:-len("Cargo.toml")].strip("/")  # ""           | "crate"
        if member and member in protected:
            info.update(resolves=False,
                        reason=(f"`{name}` is declared in {member!r}, a crate the "
                                f"instance's own patches touch — not rewritten"))
            return info
        version = nearest_version(name, requirement)
        if not version:
            info.update(resolves=False,
                        reason=f"crates.io lists no usable version of `{name}`")
            return info
        edit = {"crate": name, "from": requirement, "to": version,
                "manifest": relative}
        if not apply_retargets(container, workdir, [edit]):
            info.update(resolves=False,
                        reason=f"`{name} = \"{requirement}\"` could not be rewritten")
            return info
        info["applied"] = True
        info["retargets"].append(edit)

    info["resolves"] = resolves(container, workdir, timeout_s)[0]
    if not info["resolves"]:
        info["reason"] = f"still unresolvable after {len(info['retargets'])} rewrites"
    return info

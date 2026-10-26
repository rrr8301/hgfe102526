# Tools the agent calls during execution

This describes what runs inside the container when NumericPEVR reproduces an
HBEC instance, i.e. `pipeline.py --build-mode agent --era-mode dynamic`
(`run_one_agent.sh`). The driver is `NumericPEVR/loop.py`; the tools themselves
live in `numagent/repro_tools.py` and are imported unchanged.

## The planner does not call tools directly

The LLM never issues a function call against the container. Each iteration it
returns one structured plan (`AGENT_PLAN_SCHEMA`, `plan.py`). Every action in
that plan has a `revision` (`buggy`, `fixed` or `both`) and a `purpose`. The
harness turns each action into a tool call, checks it against the HBEC boundary
first, and executes it. The planner sees only the outcome, compacted into the
next iteration's state block. This setup has three consequences:

* **Ordering is fixed by the harness.** `_run_plan` always runs rebuild actions
  before shell commands, whatever order the plan lists them in. A plan that
  needs to look at the checkout before deciding how to build it has to be an
  inspection-only plan.
* **Some tools are only ever called by the orchestrator.** Verification,
  comparison, finalization and packaging are not things a plan can name, so no
  plan can grade itself.
* **Every refusal is recorded.** A blocked action leaves a `refusals` entry with
  its reason, so the claim that the agent never relaxed a tolerance is something
  the trace measures.

## 1. Tools the plan can invoke

| Plan action | Tool | What it does |
|---|---|---|
| `purpose: "rebuild"` (+ `recipe`, `python_version`, `pins`) | `rebuild_revision` | Builds one revision's isolated venv |
| `purpose: setup / build / test / probe / inspect` + `command` | `run_shell_command` | Runs one command in the revision's checkout |
| (diagnosis-driven, not a plan field) | `retrieve_evidence` | Asks RepoMiner for one missing fact |

### `rebuild_revision`

This is the agent's only route to building or changing an environment. In
build-mode agent, neither revision is built before the first plan, so the first
plan must contain a rebuild action for each revision. The arguments are:

* `recipe`: `compiled` (extension modules compiled into the revision's venv with
  the era's build tools) or `pure` (an editable install from the source tree with
  its test dependencies). The agent makes this choice from the context and the
  checkout. A later rebuild that omits it keeps the first choice.
* `python_version`: the CPython to build on, for example `3.12`.
* `pins`: dependency constraints such as `["numpy<2.4"]`. The harness resolves
  each one to the newest release matching it as of the fixed commit's date.
  Era pins are enforced through a constraints file, so a bare
  `pip install numpy==…` is rejected and a rebuild with `pins` is the only way to
  change a pinned dependency.

`revision: "both"` expands to one call per revision. After each build the
harness checks isolation: each interpreter must import its own revision's code,
and buggy and fixed must not resolve to the same location
(`imports_from`). A build that fails this check is reported as failed. Any build
failure ends the iteration early and sends the replanner the
`native_library_failure` diagnosis together with the reason.

### `run_shell_command`

This tool runs one command in a fresh shell whose working directory is
`/workspace/buggy` or `/workspace/fixed`. Installed packages persist across
calls, but shell state such as `export` and `cd` does not. Each command passes
through four gates before it runs:

1. **HBEC boundary** (`boundaries.check_command`). The command may change
   dependency versions, build configuration, installation commands, the runtime
   environment and system packages. It may not write to a test file (redirect,
   `tee`, `cp`/`mv`, `patch`, `sed -i`), pass tolerance flags, change the test's
   inputs or assertions, deselect the test, or `pip install` a released wheel of
   the package under test, because both revisions would then import the same
   PyPI build.
2. **Build gate** (agent mode only). A `test` command or any `pytest` command is
   refused on a revision that has not been built and verified through
   `rebuild_revision`.
3. **Installed-package guard** (`edits_installed_package`). The guard refuses
   edits, redirects or copies into `site-packages`/`dist-packages`, as well as
   `sitecustomize.py` and `.pth` shims. This blocks faking a dependency's
   version, which an agent did in numba#10088.
4. **Prepared-build guard** (`_clobbers_prepared_build`). On a revision that is
   already built, the guard refuses `pip install -e .`, `setup.py build_ext` or
   `develop`, and `python -m build`. Any of these would replace the verified venv
   with an unbuilt source tree (numba#10052).

The `purpose` label determines how the result is used. The run labelled `test`
becomes that revision's test record, which is what verification reads. The label
is checked, not trusted:

* A command labelled `test` that runs no test suite is reclassified as
  `inspect`. A suite counts as run if the command names a known runner or its
  output contains a runner summary. Without this check, an `ls` would be read as
  a passing test.
* A command labelled `inspect` that runs the target test selector is promoted to
  `test`.

The model receives the last 6,000 characters of stdout and stderr. The stored
record keeps 60,000 characters for deterministic parsing; stdout and stderr are
trimmed separately so that cargo's stderr noise cannot push out stdout's test
summary.

If a test command uses `--pyargs`, the harness first installs a small conftest
plugin into that revision's interpreter (`_ensure_pyargs_plugin`). This also
appears in the trace as a `setup` command.

### `retrieve_evidence`

The planner does not request evidence. The diagnosis policy (`diagnose.py`)
attaches an `evidence_need` when it decides the compact context cannot answer
the failure. The harness then queries RepoMiner, and the answer goes into the
next plan's prompt and into the context for later iterations. Limits:

* The budget is `--evidence-budget` calls per run, 2 in the main runs. Once it is
  spent, further requests are refused.
* A repeated need returns the cached first answer and does not use budget.
* Answers are capped at about 1,000 tokens and respect the mined view's isolation
  (`view=hbec`).

## 2. Tools only the orchestrator calls

These run after the plan's actions, in this order:

| Step | Tool / helper | Role |
|---|---|---|
| a | `run_shell_command` (`inspect`) | Confirms the developer test file exists on both revisions (`test -f …`), before the first plan |
| b | `ensure_reproducing_test_ran`, `ensure_test_on_both_revisions` (`numagent/planning.py`) | If nothing the plan ran named the patch's own test, runs it on both revisions, so a plan that tested the wrong thing still gets graded on the right test |
| c | `verify_numerical_outcome` (`verify.classify`) | Types the buggy and fixed test records as `F2P / P2P / F2F / P2F / SETUP_ERROR / INVALID`. F2P requires a genuine, bug-exposing failure on buggy, not an ImportError or collection error. A timeout counts as the defect when the report describes a hang |
| d | `_widen_to_other_cases` | On `P2P`, `F2F` or `SETUP_ERROR`, re-runs the other cases the test patch adds, in case a different case is the one that flips |
| e | `compare_execution_results` | Deterministic buggy-vs-fixed comparison against the strategy's success criterion; no LLM involved |
| f | `finalize_reproduction` | Deterministic terminal status (`F2P_REPRODUCED`, `REPRODUCED`, `NOT_REPRODUCED`, `SETUP_FAILURE`, …) |
| g | `package_numbugbench_instance` (`package.py`) | With `--package`, snapshots the live container, including both built revisions, patches and metadata, as `numbugbench/<repo>-<id>:<tag>`, records its size and deletes the snapshot |

Steps (b) to (d) run every iteration once both revisions are built. Steps (e) to
(g) run once, after the loop ends on a valid F2P, a fail→pass transition under
`f2p_only`, or the iteration cap (`--max-iterations`, 11 in the main runs).

### Numerical probes are inactive in these runs

`numagent` also defines `run_numerical_probe` and `check_reported_examples`,
which record values, NaN/Inf state and FP exceptions on each revision. The main
HBEC runs use `grading=f2p_only`, where the test transition is the only oracle.
NumericPEVR never dispatches these two tools, and the strategy's probe is shown
as "unused under f2p_only". A `purpose: "probe"` action is just a
`run_shell_command`.

## One iteration, end to end (sympy#28307, Haiku)

From `pipeline-haiku-eradyn-agent-final/sympy_28307_claude-haiku-5-5/logs/3-reproduce.trace.txt`:

```
[buggy/inspect] test -f sympy/core/tests/test_expr.py          orchestrator: (a)
[fixed/inspect] test -f sympy/core/tests/test_expr.py
PLAN (3 actions)
  1. [both/rebuild] recipe=pure, python 3.12                    -> rebuild_revision x2
       REBUILD buggy on 3.12: ok (20.2s)  pins pytest 8.4.1, pytest-timeout 2.4.0, mpmath 1.3.0
       REBUILD fixed on 3.12: ok (17.9s)
  2. [buggy/test] pytest sympy/core/tests/test_expr.py -k test_round   -> run_shell_command
       exit=124 TIMED OUT (1800s)
  3. [fixed/test] same                                                 -> run_shell_command
       exit=0  2 passed
[buggy/setup] [fixed/setup] pyargs plugin                       orchestrator
[buggy/test]  [fixed/test]  the patch's own test                orchestrator: (b)
verify_numerical_outcome -> F2P valid (hang reported, timeout counts)  (c)
compare_execution_results -> finalize: F2P_REPRODUCED                  (e, f)
package_numbugbench_instance -> numbugbench/sympy-28307:eradyn-agent (0.82 GB)  (g)
```

The whole run took one LLM call, with 5.5k input and 1.8k output tokens. The
62.8 minutes of wall time came almost entirely from the two 1,800-second buggy
timeouts: one from the plan's test and one from the orchestrator's re-run in (b).

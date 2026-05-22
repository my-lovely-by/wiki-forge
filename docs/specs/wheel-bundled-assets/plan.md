# Plan: wheel-bundled-assets

> **Implementation plan paired with `spec.md`.** The spec says *what*; the
> plan says *how, in what order, with what verification*.

- **Status:** Done
- **Spec:** [`docs/specs/wheel-bundled-assets/spec.md`](spec.md)
- **Owner:** TBD (maintainer)

## Approach

Land the contract bottom-up: packaging first (smallest blast radius —
the wheel grows by ~624 KB of source-tree assets, no behavior change
in any install mode that's exercised by today's tests), then the
resolver swap (changes `cli.py`'s import-time behavior but with a
kept `cli._KIT_ROOT` symbol so every monkeypatching integration test
stays green), then the end-to-end install-and-run smoke test
(catches the real failure mode B5 names — a fresh-install user who
has never seen the repo).

Each step is a self-contained PR. The plan reads like three fix PRs
not one big bang because:

1. Step 1 (packaging + test-infra) is pure config and is
   independently verifiable — a built wheel with the asset trees
   inside is the acceptance, regardless of whether `_KIT_ROOT`
   reads them. Bundling the `slow` pytest-marker registration into
   the same step is deliberate so step 2 and step 3's tests have
   the marker available the moment they land.
2. Step 2 (resolver) depends on step 1's wheel layout for its
   "bundled path is real" sense of confidence but can be reviewed
   entirely on the source side (the resolver's two branches and
   their priority order, plus the per-subdir validation).
3. Step 3 (install-and-run smoke) is the slow integration test; it
   wants to live in a dedicated CI job (`@pytest.mark.slow`) and
   not slow down the default `pytest -q`. Separating it into its
   own PR makes the CI-config change reviewable as a focused diff.

**Ordering rationale.** Step 1 leaves the kit functionally identical
in every install mode developers use today (editable; no one runs
the built wheel yet), while making the wheel *contain* the right
things. Step 2 is the first behavior change; once it lands, a
freshly-built wheel is functional under `pip install`. Step 3 proves
it in CI.

**Mechanism choice (force-include vs shared-data).** The retro-review
tracker named both as candidates. The plan picks **`force-include`**
with relocation into `llm_wiki_kit/_assets/<tree>/` for three
reasons:

1. `shared-data` writes to `<env>/share/...` (PEP 491 / hatchling
   docs), which is outside the `llm_wiki_kit` package's resource
   namespace; `importlib.resources.files("llm_wiki_kit")` can't see
   it without filesystem-walking the env prefix.
2. `force-include` with a target inside the package preserves the
   `importlib.resources` discipline — assets are package data,
   resolvable from the package's own resource tree.
3. The relocation prefix `_assets/` is short, conventional (the
   leading underscore signals "kit-internal, not a public Python
   submodule"), and won't collide with any current or planned
   submodule of `llm_wiki_kit`.

## Pre-conditions

- The retro-review fix PRs (#20, #21, #22) are merged (they are).
- The journal-locking spec is complete (it is, as of PR #33). No
  conflicting churn in `cli.py`'s top-of-module region.
- `pyproject.toml`'s build-backend is hatchling (it is).
- No outstanding work changes `_KIT_ROOT` or `_kit_paths` — confirm
  via `gh pr list --search "_KIT_ROOT in:title,body"` and
  `git grep -l "_KIT_ROOT" origin/main` before step 1 lands (none
  open as of branch date).
- `llm_wiki_kit/__main__.py` exists and calls `cli.main()` (it
  does — step 3's subprocess invocation depends on this).
- `python -m build` is available in the dev environment after step
  1's `dependencies.dev` update; today it isn't listed.

## Steps

1. **The wheel built from `main` contains `recipes/`, `core/`, and
   `templates/` under `llm_wiki_kit/_assets/`.**
   - **Depends on:** none.
   - **Verification mode:** goal-based — the success criterion is
     "the built wheel's namelist contains the expected paths." A
     test re-asserts a list of paths the build config already
     declares; the construction tests below are the gate.
   - **What you'll change.**
     - Add to `pyproject.toml`:
       ```toml
       [tool.hatch.build.targets.wheel.force-include]
       "recipes" = "llm_wiki_kit/_assets/recipes"
       "core" = "llm_wiki_kit/_assets/core"
       "templates" = "llm_wiki_kit/_assets/templates"
       ```
     - Add `"build>=1"` to `[project.optional-dependencies].dev`.
       The PyPA `build` tool is needed by step 1's tests and step
       3's CI job. Dev-only; not a runtime dependency.
     - Register the `slow` marker (do not flip `addopts` to filter
       it — that would surprise contributors running a specific slow
       test file via `pytest tests/integration/test_wheel_contents.py`
       and seeing `0 selected`):
       ```toml
       [tool.pytest.ini_options]
       testpaths = ["tests"]
       addopts = "-ra --strict-markers"
       markers = [
           "slow: marks tests that build/install a wheel (run via `pytest -m slow` or the wheel-acceptance CI job)",
       ]
       ```
       Existing tests are untouched (none are marked `slow` yet).
       The default-CI workflow gains an explicit `-m 'not slow'` on
       the `pytest` invocation in step 3; the wheel-acceptance CI
       workflow uses `-m slow`. Document the convention in
       `AGENTS.md` "Commands you'll need" in the same PR
       (`pytest -m slow` to opt in; `pytest -m 'not slow'` to opt
       out).
     - No `cli.py` changes in this step — the source-tree resolver
       still works in editable mode, and the wheel built here is
       not yet exercised by anyone.
   - **How you'll verify it.**
     - `tests/integration/test_wheel_contents.py` (new file, every
       test marked `@pytest.mark.slow`; runs only under `pytest -m
       slow` or the wheel-acceptance CI job):
       - `test_built_wheel_contains_recipes` — invoke
         `subprocess.run([sys.executable, "-m", "build", "--wheel",
         "--outdir", str(tmp_path)])` against the repo root, open
         the resulting wheel with `zipfile.ZipFile`, assert
         `"llm_wiki_kit/_assets/recipes/family.yaml" in zf.namelist()`
         and the same for `work-os.yaml` and `personal.yaml`.
       - `test_built_wheel_contains_core_primitive_and_every_file`
         — same wheel fixture; assert
         `llm_wiki_kit/_assets/core/primitive.yaml` is present, and
         for every file under the source-tree `core/files/` (walked
         via `Path("core/files").rglob("*")`, files only), assert
         the matching relative path is present in the wheel namelist
         under the `llm_wiki_kit/_assets/core/files/` prefix.
       - `test_built_wheel_contains_every_template_primitive` —
         parametrised over every `primitive.yaml` under
         source-tree `templates/`. For each, assert the mirrored
         path exists in the wheel namelist. Marker is collection-
         time parametrisation so a new primitive added without
         updating build config is caught.
     - Manual gate (one-shot, recorded in the PR description):
       `python -m build --wheel && python -c "import zipfile;
       print('\n'.join(p for p in
       zipfile.ZipFile(next(__import__('pathlib').Path('dist').glob('*.whl')))
       .namelist() if '_assets/' in p))"` — eyeball the relocation
       is present.
     - `pytest -q` continues to pass (no regression; the new file's
       tests are excluded by the `slow` marker).
     - `pytest -m slow tests/integration/test_wheel_contents.py`
       passes locally.

2. **`_kit_paths()` resolves correctly in every install mode the kit
   supports.**
   - **Depends on:** step 1 (the bundled `_assets/` layout is the
     fixture the resolver targets).
   - **Verification mode:** TDD — pure-function resolver with
     injectable seams; write the tests before the implementation.
   - **What you'll change.**
     - In `llm_wiki_kit/cli.py`, replace the `_KIT_ROOT =
       Path(__file__).resolve().parent.parent` line and its TODO
       comment with the following (sketch — final code may differ
       on naming or import ordering, but the branch logic and the
       `Path | None` lazy pattern are the contract):
       ```python
       import importlib.resources

       _BUNDLE_PREFIX = "_assets"
       _KIT_SUBDIRS: tuple[str, str, str] = ("recipes", "core", "templates")

       # Populated lazily by `_kit_root()` on first call. Tests
       # may `monkeypatch.setattr(cli, "_KIT_ROOT", <Path>)` to
       # override; tests/conftest.py resets to None per test.
       _KIT_ROOT: Path | None = None


       def _bundled_assets_path() -> Path | None:
           """Return the in-package bundled-assets dir, or None.

           Separate seam so tests can monkeypatch this function
           directly without faking the importlib.resources
           Traversable protocol.
           """
           traversable = importlib.resources.files("llm_wiki_kit").joinpath(_BUNDLE_PREFIX)
           candidate = Path(str(traversable))
           return candidate if candidate.is_dir() else None


       def _source_tree_kit_root() -> Path:
           """Return the source-checkout root containing the asset trees.

           Separate seam so the resolver's source-tree branch is
           monkeypatchable without touching ``cli.__file__``.
           """
           return Path(__file__).resolve().parent.parent


       def _resolve_kit_root() -> Path:
           """Resolve the directory containing recipes/, core/, templates/.

           Tried in order: bundled (wheel install) → source-tree
           (editable / source-checkout). Each candidate must contain
           ALL three subdirectories to win; a half-valid candidate
           falls through. See
           ``docs/specs/wheel-bundled-assets/spec.md``.
           """
           bundled = _bundled_assets_path()
           if bundled is not None and all((bundled / s).is_dir() for s in _KIT_SUBDIRS):
               return bundled
           source = _source_tree_kit_root()
           if all((source / s).is_dir() for s in _KIT_SUBDIRS):
               return source
           raise WikiError(
               "kit assets not found: neither the bundled "
               f"{_BUNDLE_PREFIX}/ nor the source-checkout root "
               f"contains all of {', '.join(_KIT_SUBDIRS)}/"
           )


       def _kit_root() -> Path:
           """Lazy accessor; populates ``_KIT_ROOT`` on first call.

           Production code reads the kit root via this function,
           never via the module attribute. Tests that
           `setattr(cli, "_KIT_ROOT", kit)` cause this function to
           return the test value (the lazy check is
           `if _KIT_ROOT is None`).
           """
           global _KIT_ROOT
           if _KIT_ROOT is None:
               _KIT_ROOT = _resolve_kit_root()
           return _KIT_ROOT
       ```
       Notes on the sketch:
       - **Why lazy?** Round-2 adversarial review flagged that
         eager `_KIT_ROOT = _resolve_kit_root()` at import time
         crashes `wiki --version` / `wiki --help` if the wheel is
         misconfigured — exactly the diagnostics a user runs to
         figure out why the kit is broken. Lazy resolution defers
         the WikiError until the first asset-touching call, where
         the CLI surface's `try/except WikiError` block catches it
         cleanly.
       - **Why `Path | None` and not PEP 562 `__getattr__`?**
         Round-3 adversarial review flagged that raising
         `WikiError` from `__getattr__` propagates through
         `hasattr` and `getattr(default)` (which expect
         `AttributeError`), creating an indirect contract leak.
         The simpler `_KIT_ROOT: Path | None = None` pattern keeps
         attribute reads side-effect-free; reads return `None`
         when unresolved. The one current direct-read site
         (a symlink-construction fixture in
         `tests/integration/test_wiki_init.py`) migrates to
         `_kit_root()` in this step. Test-set values via
         `monkeypatch.setattr` still win because the lazy check is
         `if _KIT_ROOT is None`.
       - **Why two seam helpers?** `_bundled_assets_path()` and
         `_source_tree_kit_root()` exist so the resolver's tests
         can replace each branch uniformly via
         `monkeypatch.setattr(cli, "_bundled_assets_path", ...)` /
         `_source_tree_kit_root`. The alternative (monkeypatching
         `cli.__file__` for the source-tree branch) is fragile and
         interacts with importlib/traceback machinery.
       - **Why not `with importlib.resources.as_file(...)`?** For
         a real-filesystem package (the kit's target install mode),
         `Path(str(traversable))` round-trips cleanly without the
         context-manager footgun (returning a path out of an
         `as_file` block can invalidate it on zip-backed loaders).
         Zipapp support is a §Non-goal. Namespace-package layout
         (where `importlib.resources.files` returns a
         `MultiplexedPath`) also produces a `str()` that
         `is_dir()` rejects — the resolver falls through to the
         source-tree branch, which under a namespace-package
         install also fails to validate, producing the generic
         "kit assets not found" `WikiError`. Acceptable per spec
         §Non-goals (converting `llm_wiki_kit/` to a namespace
         package would be an RFC).
       - **No `@functools.cache`** — the "cache" is the
         `_KIT_ROOT` module attribute itself. This simplifies test
         isolation (one knob to reset, not a cache_clear() obligation
         on every test).
     - In `cli.py`, change every direct read of `_KIT_ROOT` to a
       call to `_kit_root()`. Today there are two such sites:
       `_kit_paths()` (line ~109) and `_cmd_doctor`'s
       `run_doctor(vault_root, _KIT_ROOT)` (line ~394). Both
       receive the same lazy behavior; integration tests that
       `monkeypatch.setattr` continue to work because
       `_kit_root()`'s `if _KIT_ROOT is None` check sees the
       monkeypatched non-`None` value and returns it.
     - In `tests/conftest.py` (new file), add a function-scoped
       autouse fixture:
       ```python
       from collections.abc import Iterator

       import pytest

       from llm_wiki_kit import cli


       @pytest.fixture(autouse=True)
       def _reset_lazy_kit_root() -> Iterator[None]:
           """Reset cli._KIT_ROOT to None before each test.

           The kit's resolver caches in a module-level attribute
           (not @functools.cache, see docs/specs/wheel-bundled-assets/
           spec.md). Without per-test reset, a unit test that
           monkeypatches _bundled_assets_path to a tmp dir leaves
           the attribute pointing at a deleted tmp for subsequent
           tests. This fixture isolates that.
           """
           cli._KIT_ROOT = None
           yield
           cli._KIT_ROOT = None
       ```
       Load-bearing: this is the test-isolation infrastructure
       the lazy pattern requires, not the speculative env-var
       fixture an earlier draft proposed.
     - In `tests/integration/test_wiki_init.py`, change the
       symlink-construction fixture (where it reads
       `cli._KIT_ROOT / "core"` and `cli._KIT_ROOT / "templates"`)
       to call `cli._kit_root()` instead. One-line touch per
       read.
   - **How you'll verify it.**
     - `tests/unit/test_cli_kit_root.py` (new file). The tests'
       names match spec §Acceptance criteria → Resolver exactly;
       no extras. The `_reset_lazy_kit_root` autouse fixture from
       `tests/conftest.py` handles per-test `_KIT_ROOT = None`
       reset, so individual tests don't need to repeat it:
       - `test_resolve_kit_root_prefers_bundled_assets_when_present`
         — monkeypatch `cli._bundled_assets_path` to return a
         `tmp_path / "bundle"` directory containing
         `recipes/`, `core/`, `templates/` (all empty dirs).
         Assert `_resolve_kit_root() == tmp_path / "bundle"`.
       - `test_resolve_kit_root_validates_bundled_subdirs_before_returning`
         — monkeypatch `_bundled_assets_path` to return a
         directory missing `recipes/`. Monkeypatch
         `_source_tree_kit_root` to return a `tmp_path` whose three
         subdirs exist. Assert the resolver returns the source-tree
         path (i.e., the bundle was rejected and the fallback won).
       - `test_resolve_kit_root_falls_back_to_source_tree_when_no_bundle`
         — monkeypatch `_bundled_assets_path` to return `None`.
         Monkeypatch `_source_tree_kit_root` to return a `tmp_path`
         whose three subdirs exist. Assert the resolver returns
         that path.
       - `test_resolve_kit_root_raises_wikierror_when_no_branch_resolves`
         — monkeypatch `_bundled_assets_path` to return `None`.
         Monkeypatch `_source_tree_kit_root` to return a `tmp_path`
         with no asset subdirs. Assert `WikiError` is raised;
         assert the message names the missing subdir set
         (substring match on `'recipes, core, templates'`).
       - `test_kit_root_helper_resolves_lazily_and_caches` —
         set `cli._KIT_ROOT = None` (the autouse fixture already
         did this; explicit for clarity). Replace
         `cli._resolve_kit_root` with a counter+delegate that
         calls the original and increments a `list[int]` shared
         with the test. Assert: counter == 0; first `cli._kit_root()`
         call returns a `Path` and counter == 1 and
         `cli._KIT_ROOT is not None`; second `cli._kit_root()`
         call returns the same `Path` and counter is still 1
         (the lazy check short-circuits).
     - Full `pytest -q` still passes — every existing integration
       test that monkeypatches `cli._KIT_ROOT` continues to work
       because the symbol is still there.
     - `ruff check llm_wiki_kit/ tests/`, `ruff format --check
       llm_wiki_kit tests`, `mypy llm_wiki_kit tests`.

3. **`pip install <wheel>` followed by `wiki init --recipe family`
   produces a working vault.**
   - **Depends on:** step 1 (the wheel must contain the assets) and
     step 2 (the resolver must find them in the installed package).
   - **Verification mode:** goal-based — the success criterion is
     end-to-end (a fresh wheel install renders a vault). Subprocess
     invocation; manual gate complements the automated test.
   - **What you'll change.**
     - `tests/integration/test_wheel_install_end_to_end.py` (new
       file, marked `@pytest.mark.slow`):
       - `test_pip_install_wheel_then_wiki_init_renders_a_vault` —
         a session-scoped fixture builds the wheel once into a
         tmp directory (factored into `tests/integration/conftest.py`
         so step 1's `test_wheel_contents.py` can also reuse it).
         Per-test, install it into a fresh
         `pip install --target=<tmp-prefix> --no-deps --no-cache-dir <wheel>`
         directory (`--no-deps` keeps the install closed against
         network and avoids pulling in pyyaml/pydantic since the
         test env already has them). Then invoke
         `[sys.executable, "-m", "llm_wiki_kit", "init",
         str(tmp_vault), "--recipe", "family"]` as a subprocess
         with `env={"PYTHONPATH": str(tmp_prefix), **os.environ}`.
         (`llm_wiki_kit/__main__.py` already exists and calls
         `cli.main()` — see Pre-conditions.) Assert: exit code 0,
         `<tmp-vault>/.wiki.journal/journal.jsonl` exists, the
         journal parses via `read_events`, at least one
         `VaultInitEvent` and one `PrimitiveInstallEvent` are
         present.
     - A `.github/workflows/wheel-acceptance.yml` (new) running
       `pytest -m slow` on every PR that touches `pyproject.toml`,
       `llm_wiki_kit/**`, `recipes/`, `core/`, `templates/`, or
       `.github/workflows/wheel-acceptance.yml` itself (so edits to
       the workflow re-trigger the workflow). The filter is
       intentionally broad on `llm_wiki_kit/**` because
       the end-to-end smoke test exercises `recipes.py`,
       `primitives.py`, `render.py`, `install.py`, and the journal
       stack — a change to any of them that breaks render under a
       real wheel install would otherwise slip through. Wheel build
       is the slow bit (~3-5s) and runs once per PR triggered. If
       the reviewer prefers collapsing this into an existing
       workflow (e.g. a conditional matrix entry in `tests.yml`),
       the test still runs in CI on every touching PR — that's the
       requirement. Also add a nightly cron run as backstop for
       changes to files outside the path filter (e.g. a stdlib
       version bump in CI image).
     - Flip status:
       - `docs/specs/wheel-bundled-assets/spec.md` frontmatter
         from `Status: Draft` → `Status: Implemented`.
       - This `plan.md` frontmatter from `Status: Drafting` →
         `Status: Done`.
       - Tick the B5 checkbox on issue
         [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23).
       - Update `docs/architecture/overview.md` §"The Python
         package" (one paragraph: bundling convention, pointer to
         this spec).
   - **How you'll verify it.**
     - `pytest -m slow tests/integration/test_wheel_install_end_to_end.py`
       passes locally against a freshly-built wheel.
     - CI run on the PR shows the new workflow / job is green on
       a clean runner.
     - Manual gate (one-shot, recorded in PR description):
       `pipx run --spec dist/llm_wiki_kit-*.whl wiki init /tmp/v
       --recipe family` runs to completion in a shell with no
       checkout in scope. (Confirms no developer-dotfile is
       silently providing the asset paths.)

## Verification gate

The whole plan succeeds when:

```
python -m build --wheel                              # produces dist/llm_wiki_kit-*.whl
pytest -m 'slow or not slow'                         # full coverage of both partitions
pytest tests/unit/test_cli_kit_root.py               # resolver tests pass standalone
ruff check llm_wiki_kit/ tests/
ruff format --check llm_wiki_kit tests
mypy llm_wiki_kit tests
```

The `-m 'slow or not slow'` form is deliberate: it runs every test
regardless of marker, catching an accidentally-unmarked slow test
(which would otherwise live only in the `-m slow` partition and
slip past contributors running plain `pytest`).

And the spec's acceptance-criteria checkboxes are all ticked.

## Risks

- **Hatchling's `force-include` semantics on editable installs.**
  Editable mode does not materialize force-included paths in the
  importable tree (hatchling's documented behavior; the editable
  target uses a `.pth` file that points at the source root). The
  spec accepts this: editable installs read the source-tree top-
  level dirs via the resolver's second branch. *Recovery:* if a
  future hatchling release changes editable semantics to honor
  `force-include`, the resolver's branch order still works
  (bundled is checked first and would now succeed in editable mode
  too — and it'd point at the same files via the relocation, which
  is correct). No code change needed.
- **`importlib.resources.files()` on a namespace package.**
  `llm_wiki_kit/__init__.py` exists, so the package is a regular
  package and `files()` returns a real `PosixPath`/`WindowsPath`-
  flavored Traversable that round-trips through `str()`. *Recovery:*
  if anyone converts `llm_wiki_kit/` to a namespace package (i.e.
  removes `__init__.py`), the resolver needs a rethink — and that
  conversion is an RFC, not a silent refactor. Spec §Non-goals
  names this.
- **CI cost of the wheel build + install round-trip.** A wheel
  build is ~2-3s; `pip install --target` is ~1s; the subprocess
  `wiki init` is ~1s. Total per test ~5-7s. Acceptable as a
  `slow`-marked job that runs on path-filtered PRs only.
  *Recovery:* if it slows the wheel-touching PR loop, cache the
  built wheel between runs via `actions/cache` keyed on the source
  tree hash.
- **Wheel size growth.** `recipes/` + `core/` + `templates/` is
  ~624 KB on disk; the resulting wheel grows by roughly the
  zip-compressed equivalent (~150-250 KB). Negligible against the
  pydantic-v2 + pyyaml dependency footprint pulled in transitively.
  *Recovery:* none needed. If a future spec adds materially larger
  assets (e.g. binary models for ingest), that's a separate
  packaging decision.
- **Existing integration tests that symlink the asset trees.**
  `tests/integration/test_wiki_init.py:62-63` builds a custom tmp
  kit root and symlinks `core` / `templates` from
  `cli._KIT_ROOT`. After step 2, `cli._KIT_ROOT` points at the
  bundled `_assets/` dir in wheel mode and the repo root in
  editable mode; tests run under editable, so behavior is
  unchanged. *Recovery:* when step 2 lands, add a one-line
  comment at those symlink sites naming the dual-mode behavior so
  a future maintainer doesn't get confused.
- **Environment variable leak from a developer dotfile.** No
  env-var override is defined by this spec (see §Out of scope), so
  there's no leak surface today. *Recovery:* if a future spec adds
  an override env var, that spec also lands a session-scoped
  autouse fixture in `tests/conftest.py` to unset it during tests.
  This spec deliberately does NOT ship that fixture preemptively
  (round-2 adversarial review caught the dead-code shape).

## Out of scope

- **Threading `kit_root: Path | None` through `cli.main` /
  `build_parser`.** That's `qC8`. Tracked on issue #23; gets its
  own spec/PR once B5 ships.
- **A `WIKI_KIT_ROOT` (or similar) env-var override** for vendored
  or fork-bundled assets. Deferred per spec §Non-goals; an earlier
  draft included it as an "escape hatch" but adversarial review
  flagged it as scope creep with no demand signal. If a real
  user asks, a follow-up spec adds both the env-var override
  *and* an autouse fixture in `tests/conftest.py` to keep
  developer-dotfile env state from leaking into tests, in the
  same PR.
- **Bundling `examples/`, `docs/`, `tests/`, or `tools/`.** Not
  runtime assets.
- **Deriving the orphan-territory set from journaled writes.**
  That's `qC10 + C6`. Shipped in its own retro-cleanup PR on
  issue #23.
- **Migrating the integration tests' monkeypatch pattern.** The
  symbol is preserved precisely to avoid touching ~6 test files in
  this spec; qC8 owns that migration.
- **Publishing to PyPI.** This spec gates *being able to* publish;
  the publish itself is a release-engineering decision, not in
  scope.
- **A `wiki kit-root` introspection subcommand.** `wiki doctor`
  output (or a one-line `python -c "from llm_wiki_kit.cli import
  _KIT_ROOT; print(_KIT_ROOT)"`) covers the diagnostic case. If
  real users ask, follow-up spec.

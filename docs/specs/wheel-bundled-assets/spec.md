# Spec: wheel-bundled-assets

> **Living document.** Updated alongside the code. Drift between spec and
> code is a bug — fix the code or the spec in the same PR.

- **Status:** Implemented
- **Owner:** `pyproject.toml` + `llm_wiki_kit.cli`
- **Related:** [RFC-0001](../../rfc/0001-v2-architecture.md) §"Phase E — release", [`docs/specs/wheel-bundled-assets/plan.md`](plan.md), retro-review issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23) (finding **B5**, "Wheel install ships no `recipes/`, no `core/`, no `templates/`").
- **Blocks:** the v2.0.0 release on PyPI. Today's wheel installs cleanly
  but every `wiki init` / `wiki add` against it raises `FileNotFoundError`
  on the recipes directory.

## What this is

The kit's runtime needs three asset trees alongside the Python package:

- `recipes/` — the YAML recipes (`family.yaml`, `work-os.yaml`,
  `personal.yaml`) `wiki init --recipe <name>` reads.
- `core/` — the always-installed common-core primitive
  (`primitive.yaml` + `files/`).
- `templates/` — the primitive catalog (`ontologies/`, `content-types/`,
  `operations/`, `infrastructure/`).

Today these live at the repo root as siblings of `llm_wiki_kit/`.
`pyproject.toml`'s `[tool.hatch.build.targets.wheel]` `packages =
["llm_wiki_kit"]` configures hatchling to ship only the Python
package — **none of the three asset trees make it into the wheel**.
`cli.py`'s module-level `_KIT_ROOT = Path(__file__).resolve().parent.parent`
resolves to:

- Under `pip install -e .` (the contributor path) resolves to the repo
  root, where the asset trees do exist — so every test, every dev
  invocation, every CI run today works fine.
- Under a real wheel install (`pip install llm-wiki-kit` from PyPI, or
  `pip install dist/llm_wiki_kit-*.whl`) resolves to `site-packages/`,
  where the asset trees **do not exist** — so `wiki init --recipe
  family` raises `FileNotFoundError: site-packages/recipes/family.yaml`
  on the first call into `load_recipe`.

This spec defines the contract for shipping the kit as a real installable
wheel: the three asset trees are bundled into the wheel as kit-package
data via a stable build-time relocation; the kit resolves them at
runtime using `importlib.resources`, with a documented editable-install
fallback that reads the source tree; the test surface gains a wheel
build-and-install acceptance test that pins the contract for v2.0.0.

This spec covers **packaging and asset resolution.** It does not change
the layout of the source tree (the asset trees stay at the repo root
for catalog-editing ergonomics).

`qC8` from issue [#23](https://github.com/eugenelim/llm-wiki-kit/issues/23)
— threading `kit_root: Path | None` through `cli.main` / `build_parser`
to retire the integration-test `monkeypatch.setattr(cli, "_KIT_ROOT", …)`
pattern — was originally listed as a §Non-goal here and deferred to its
own spec. Both touch `cli.py:_KIT_ROOT`; bundling them landed in the
same PR. The §Acceptance criteria below pick up two additional bullets
(``_kit_paths`` accepts an explicit override; the grep guard pins no
remaining direct reads of ``_KIT_ROOT``); the §Invariant "module-level
attribute is preserved as a monkeypatch seam" is intentionally
weakened by qC8 — the attribute is now the lazy cache only, never read
directly from outside the resolver block.

## Inputs

- **Wheel build** — `python -m build --wheel`. Configured by
  `pyproject.toml`'s `[tool.hatch.build.targets.wheel]` section.
- **Editable install** — `pip install -e .`. Hatchling's editable
  target reads the same configuration but does not materialize the
  relocated paths inside the package directory; the source-tree
  layout is what Python sees.
- **`cli._kit_paths(kit_root: Path | None = None) -> tuple[Path, Path, Path]`**
  — the one resolver every kit-asset-touching CLI handler reaches
  through (`_cmd_init`, `_cmd_add`, `_cmd_ingest`). Returns
  `(recipes_dir, core_dir, templates_dir)`; callers don't know or
  care whether the underlying assets live in `site-packages/` or in
  the repo. `_cmd_doctor` takes a single root (not three derived
  paths) and instead reads `args.kit_root if args.kit_root is not
  None else cli._kit_root()` directly, since `run_doctor`'s signature
  is `(vault_root, kit_root)`. `_cmd_resolve` does not touch kit
  assets at all and never calls either helper.

## Outputs

- **`pip install llm-wiki-kit==<version>` produces a usable kit.** After
  install, `wiki init --recipe family /tmp/v` renders a working vault
  with at least one `PrimitiveInstallEvent` per recipe-listed primitive
  and no `FileNotFoundError` on any asset path.
- **`_kit_paths()` returns three on-disk directory `Path`s** that
  satisfy `is_dir()` in every install mode the kit supports (wheel,
  editable, source-checkout `python -m llm_wiki_kit`). The return
  tuple's shape and type are unchanged.
- **The built wheel contains every recipe YAML at the repo root,
  every catalog primitive's `primitive.yaml` under `templates/`, and
  the entirety of `core/files/`** — the wheel's zipfile namelist
  mirrors today's source-tree contents of the three asset trees
  under an in-package `_assets/` prefix (i.e.,
  `llm_wiki_kit/_assets/recipes/...`,
  `llm_wiki_kit/_assets/core/...`,
  `llm_wiki_kit/_assets/templates/...`). The prefix is the resource
  namespace `importlib.resources.files("llm_wiki_kit")` reads from
  at runtime; it is observable to anyone running `python -m zipfile
  -l <wheel>` and is treated as part of the contract.
- **No new runtime dependency.** `importlib.resources` is stdlib;
  `pyproject.toml`'s `dependencies = ["pyyaml>=6", "pydantic>=2"]`
  stays exactly as written. (Per `AGENTS.md` "Runtime dependencies",
  adding one would require its own ADR.)

## Behavior

### Happy path — wheel install

1. `pip install llm-wiki-kit` unpacks the wheel into the active
   environment's `site-packages/`.
2. `wiki --version` and `wiki --help` run without touching the asset
   resolver. Asset resolution is **lazy**: the `cli._KIT_ROOT` module
   attribute is initialized to `None` at import; the
   `cli._kit_root()` helper populates it on first call. Production
   code reads the kit root via `_kit_root()` (not by reading the
   attribute directly), so import-time failures don't break
   diagnostics.
3. The first kit-asset-touching subcommand (`wiki init`, `wiki add`,
   `wiki ingest`, `wiki resolve`, `wiki doctor`) calls `_kit_paths()`,
   which calls `_kit_root()`.
4. `_kit_root()` calls `_resolve_kit_root()` once (subsequent calls
   return the cached `_KIT_ROOT` value). The resolver checks the
   bundled-asset location:
   `importlib.resources.files("llm_wiki_kit")` returns the installed
   package directory; the resolver looks for the relocated trees
   under `_assets/` inside it. For a wheel install this directory
   exists and contains all three subdirectories; the resolver
   returns it as a `Path`, which `_kit_root()` writes to `_KIT_ROOT`.
5. The three returned paths (`<root>/recipes`, `<root>/core`,
   `<root>/templates`) feed into `load_recipe`, `load_primitive`,
   `discover_primitives`, and `discover_recipes` exactly as today.

### Happy path — editable install (contributor workflow)

1. `pip install -e .[dev]` configures hatchling's editable target;
   Python imports `llm_wiki_kit` from the source tree.
2. The resolver checks the bundled-asset location first. In editable
   mode the relocated trees are not materialized inside the package
   directory (hatchling's documented editable-target behavior — see
   plan §Risks); the bundled-asset directory does not exist.
3. The resolver falls back to `Path(__file__).resolve().parent.parent`
   — the repo root, validated by checking that `recipes/`, `core/`,
   and `templates/` all exist there. The three sibling directories
   exist as normal source-controlled directories. Resolution
   succeeds.

### Happy path — `python -m llm_wiki_kit` from a source checkout

Same as editable install. `__file__` resolves to
`<checkout>/llm_wiki_kit/cli.py`; `__file__.parent.parent` is the
checkout root, and the source-tree branch fires.

### Edge cases

- **Wheel built with the old `packages = ["llm_wiki_kit"]` config and
  no relocation.** The package directory exists in `site-packages/`
  but the bundled-asset directory does not. The resolver's bundled
  branch fails; the source-tree fallback's
  `Path(__file__).resolve().parent.parent` is `site-packages/`, where
  `recipes/`, `core/`, `templates/` do not exist; the resolver's
  three-subdir validation fails and the resolver raises a one-line
  `WikiError` (see §Error cases). This is the **pre-spec failure
  mode**; the fix is to publish a new wheel built under this spec.
- **Wheel built under this spec but a build bug omits one of the
  asset trees** (e.g. `core/` was excluded by an accidental
  `exclude` pattern). The bundled-asset directory exists but is
  missing a subdirectory. The resolver's per-subdir validation
  catches it and raises before any `load_recipe` call. The wheel
  acceptance test (§Acceptance criteria → Wheel contents) catches
  this in CI before publish, so the runtime failure mode is the
  *second* line of defense.
- **`importlib.resources.files()` against a namespace package.**
  `llm_wiki_kit` is a regular package (it has `__init__.py`), so
  `files()` returns a real `PosixPath` / `WindowsPath`-flavored
  Traversable that round-trips through `str()` and back to a real
  filesystem `Path`. The spec assumes regular-package layout;
  converting the package to a namespace package would invalidate
  the resolver and is an RFC-level change (see §Non-goals).
- **Zipapp / zipped wheel install.** `importlib.resources.files`
  works against a zip-backed package but yields a `zipfile.Path`
  Traversable, not a real filesystem path. The kit's downstream
  callers (`load_recipe`, `discover_primitives`) take a
  `pathlib.Path` and call `path.open()` / `path.iterdir()`; the
  type contract is `pathlib.Path`. **Zipapp installs are out of
  scope for v2.0.0** (see §Non-goals). Under a zipapp install the
  resolver's bundled-path probe fails the `is_dir()` check (the
  stringified `zipfile.Path` is not a filesystem directory), and
  the source-tree fallback also fails (the source checkout isn't
  present at runtime); the result is the generic "kit assets not
  found" `WikiError` (see §Error cases). A zipapp-specific error
  message is deliberately not added — diagnosis-of-cause is left
  to the user, since the supported install path is plain `pip
  install <wheel>`.
- **Symlinks involving the asset trees.** The repo's source tree
  contains no symlinks *inside* `recipes/`, `core/`, or
  `templates/`. Integration tests at `tests/integration/test_wiki_init.py`
  create symlinks *pointing at* those trees from inside a tmp kit
  root; hatchling follows symlinks at build time, matching today's
  behavior, and the editable-mode resolver doesn't see those
  test-created symlinks because tests run under editable install.
  If a future contributor adds a symlink to the source-tree asset
  trees, the wheel acceptance test (§Acceptance) catches whatever
  divergence results.

### Error cases

- **Resolver returns no valid path in any branch** (bundled missing
  or invalid; source-tree fallback also missing the three
  subdirectories). Raises `WikiError("kit assets not found: neither
  the bundled <prefix>/ nor the source-checkout root contains
  recipes/, core/, templates/")`. The CLI surface catches and prints;
  exit `WIKI_ERROR_EXIT` (2).
- **Asset tree exists but a single asset file is missing at runtime**
  (e.g. a user deleted `recipes/family.yaml` from inside the wheel
  install). The kit raises whatever the existing call surface raises
  today (`load_recipe` → `FileNotFoundError`; `discover_primitives` →
  empty list). Not in scope for this spec; the kit's existing
  behavior is the contract.

## Invariants

- **`_kit_paths()` returns three on-disk directory `Path`s in every
  install mode the kit supports.** `recipes_dir.is_dir() and
  core_dir.is_dir() and templates_dir.is_dir()` after the call. This
  invariant holds for wheel, editable, and source-checkout installs.
- **Asset resolution is lazy.** `import llm_wiki_kit.cli` does not
  read the filesystem to find the asset trees. `cli._KIT_ROOT` is
  `Path | None` (initialized to `None`); `cli._kit_root()` calls
  `_resolve_kit_root()` on first invocation and assigns the result.
  A misconfigured wheel produces a clean `WikiError` from the CLI
  surface on the first asset-touching subcommand, not a Python
  traceback at import time. `wiki --version` and `wiki --help`
  therefore stay usable for diagnosis. Production code reads the
  root via `_kit_root()`, never via the attribute directly.
- **`pyproject.toml`'s `dependencies` list is unchanged.** No new
  runtime dependency. `importlib.resources` is stdlib in 3.11+
  (`requires-python = ">=3.11"`).
- **The source tree's top-level layout is unchanged.** `recipes/`,
  `core/`, `templates/` stay where they are; the wheel's relocation
  prefix is a build-time concern only. Editing a primitive or recipe
  still touches the same path it does today.
- **The `cli._KIT_ROOT` module-level attribute is the lazy cache and
  nothing else.** Production code reads through `_kit_paths()` /
  `_kit_root()`; tests pass an explicit override via
  `cli.main(argv, kit_root=...)`. The qC8 grep guard at
  `tests/unit/test_cli_kit_root.py::test_kit_root_is_not_referenced_outside_kit_paths_helper`
  pins the *cross-file* boundary — any reference to the identifier
  `_KIT_ROOT` from outside the allow-list `{cli.py, tests/conftest.py,
  tests/unit/test_cli_kit_root.py}` fails the test. Intra-cli.py
  discipline (the resolver block being the only reader) is convention,
  not gate-enforced; the resolver block is short enough that a stray
  read would be obvious at PR-review time. The function-scoped autouse
  fixture in `tests/conftest.py` resets `cli._KIT_ROOT = None` per
  test so a unit test that monkeypatches `_bundled_assets_path` to a
  tmp dir cannot leak a stale cache into the next test.
- **Production code reads the kit root via `_kit_root()` or
  `_kit_paths()`, never via the attribute directly.** A read of
  `cli._KIT_ROOT` may return `None` (pre-resolution). The one current
  direct-read site (`tests/integration/test_wiki_init.py`'s symlink
  construction in the tmp-kit-root fixture) migrated to
  `cli._kit_root()` in the same change that introduced lazy
  resolution.

- **`cli.main(argv, kit_root: Path | None = None)` is the
  test-and-vendor override seam.** When supplied, the path is
  written to `args.kit_root` after parsing and every kit-asset-
  touching handler (`_cmd_init`, `_cmd_add`, `_cmd_doctor`,
  `_cmd_ingest`) threads it into `_kit_paths(args.kit_root)`. When
  `None`, `_kit_paths()` falls through to the lazy resolver. This
  is the qC8 contract: integration tests pass `kit_root=tmp_kit`
  rather than monkey-patching the module attribute.

## Contracts with other modules

- **`pyproject.toml`** gains a hatchling `force-include` block
  that bundles `recipes/`, `core/`, and `templates/` into the
  wheel under the in-package `_assets/` prefix. The
  `[project.optional-dependencies].dev` extras gain `build` (the
  PyPA `build` tool; dev-only, not runtime). The
  `[tool.pytest.ini_options]` table gains a `markers = ["slow:
  ..."]` registration; the `addopts` is **not** flipped to filter
  the marker (contributors running a specific slow test file
  shouldn't see `0 selected`). Default CI invokes `pytest -m
  'not slow'`; the wheel-acceptance CI workflow invokes `pytest
  -m slow`.
- **`cli.py`** swaps eager `_KIT_ROOT =
  Path(__file__).resolve().parent.parent` for `_KIT_ROOT: Path | None
  = None` plus three new helpers: `_bundled_assets_path()` returns
  the in-package `_assets/` directory or `None`;
  `_source_tree_kit_root()` returns
  `Path(__file__).resolve().parent.parent`; `_resolve_kit_root()`
  consults both and validates the three-subdir contract. A
  `_kit_root()` accessor populates `_KIT_ROOT` on first call.
  `_kit_paths(kit_root: Path | None = None)` reads via
  `_kit_root()` when no override is passed; otherwise returns
  `kit_root / "recipes"`, `kit_root / "core"`,
  `kit_root / "templates"` without consulting the cache. `cli.main`
  gains the keyword-only `kit_root` argument and writes it to
  `args.kit_root` so handlers can thread it. `_cmd_init`, `_cmd_add`,
  and `_cmd_ingest` switch to reading via `_kit_paths(args.kit_root)`;
  `_cmd_doctor` reads
  `args.kit_root if args.kit_root is not None else _kit_root()` and
  passes that single root to `run_doctor` (whose signature takes one
  root, not three derived paths).
- **`tests/conftest.py`** gains a function-scoped autouse fixture
  that resets `cli._KIT_ROOT = None` before each test. This isolates
  the lazy-cache between tests and is load-bearing (not the
  speculative env-var fixture an earlier draft proposed and then
  deleted).
- **All seven integration test files** that used the
  `monkeypatch.setattr(cli, "_KIT_ROOT", kit)` pattern migrate to
  `cli.main(argv, kit_root=kit)` (qC8). The symlink-construction
  fixture in `tests/integration/test_wiki_init.py` migrates its
  one direct read of `cli._KIT_ROOT` to `cli._kit_root()` in the
  same change.
- **No other module changes.** `primitives.py`, `recipes.py`,
  `install.py`, `doctor.py`, `render.py` continue to consume the
  paths `_kit_paths()` returns without knowing where they came from.
- **`docs/architecture/overview.md` §"The Python package"** gains a
  one-paragraph note: "The kit bundles `recipes/`, `core/`, and
  `templates/` into the wheel via a hatchling relocation
  (see [`docs/specs/wheel-bundled-assets/spec.md`](../specs/wheel-bundled-assets/spec.md)).
  The source tree keeps them at the top level; the wheel relocates
  them into the package via build-time configuration. `cli._KIT_ROOT`
  resolves the right location for both install modes."

## Acceptance criteria

The same list translates 1-to-1 into the construction tests in
[`plan.md`](plan.md) §Steps. Plan tests have the same names as the
checkboxes below; the plan adds no tests that aren't listed here.

### Wheel contents (B5 core)

- [x] `test_built_wheel_contains_recipes` — `python -m build --wheel`
      produces a wheel whose `zipfile.ZipFile.namelist()` includes
      every recipe YAML at `<bundle-prefix>/recipes/<name>.yaml` for
      `name` ∈ {`family`, `work-os`, `personal`}.
- [x] `test_built_wheel_contains_core_primitive_and_every_file` —
      the wheel lists `<bundle-prefix>/core/primitive.yaml` plus
      every file under `core/files/` at build time (asserted by
      walking the source-tree `core/files/` and checking each
      relative path is present in the wheel namelist under the
      relocation prefix). A `force-include`/`shared-data` config
      that ships only a sentinel file would fail this test.
- [x] `test_built_wheel_contains_every_template_primitive` —
      parametrised over the union of `templates/<kind>/<name>/`
      directories with a `primitive.yaml` discovered on disk; the
      wheel namelist contains each one at the corresponding
      `<bundle-prefix>/templates/<kind>/<name>/primitive.yaml`. A
      primitive added to the source tree without updating the
      build configuration would fail this test (the relocation
      block should be tree-wide, so this guards against accidental
      `exclude` regressions).

### Resolver (`_resolve_kit_root`)

- [x] `test_resolve_kit_root_prefers_bundled_assets_when_present` —
      injected `_bundled_assets_path()` returns a tmp dir containing
      `recipes/`, `core/`, and `templates/`; resolver returns it.
- [x] `test_resolve_kit_root_validates_bundled_subdirs_before_returning`
      — injected `_bundled_assets_path()` returns a dir missing
      `recipes/`; resolver falls through to the source-tree branch
      rather than returning a half-valid bundle.
- [x] `test_resolve_kit_root_falls_back_to_source_tree_when_no_bundle`
      — injected `_bundled_assets_path()` returns `None`; injected
      `_source_tree_kit_root()` returns a tmp dir whose three
      subdirs exist; resolver returns that path.
- [x] `test_resolve_kit_root_raises_wikierror_when_no_branch_resolves`
      — injected `_bundled_assets_path()` returns `None`; injected
      `_source_tree_kit_root()` returns a tmp dir missing the three
      subdirs; resolver raises `WikiError` naming the missing
      subdir set.
- [x] `test_kit_root_helper_resolves_lazily_and_caches` — set
      `cli._KIT_ROOT = None`; replace `cli._resolve_kit_root` with
      an instrumented counter+delegate; call `cli._kit_root()`
      twice; assert the counter is 1 (not 2) and
      `cli._KIT_ROOT is not None` after the first call. Pins
      the lazy-and-cached contract via the public helper, not via
      module-reload gymnastics.

### qC8 — kit-root threading

- [x] `test_kit_paths_uses_explicit_override_without_consulting_resolver`
      — `_kit_paths(kit_root=<tmp>)` returns the three derived paths
      without ever calling `_resolve_kit_root`; the module-level
      `_KIT_ROOT` cache remains `None`.
- [x] `test_cli_main_threads_kit_root_into_args_namespace` —
      `cli.main(argv, kit_root=<tmp>)` writes the value to
      `args.kit_root`; omitting the kwarg sets `args.kit_root` to
      `None`.
- [x] `test_kit_root_is_not_referenced_outside_kit_paths_helper` —
      grep guard: scans `llm_wiki_kit/` and `tests/` for the
      identifier `_KIT_ROOT`; permits only `cli.py` (the helper),
      `tests/conftest.py` (the reset fixture), and
      `tests/unit/test_cli_kit_root.py` (this file). Any other
      reference is the qC8 antipattern.

### End-to-end install + run (B5 acceptance)

- [x] `test_pip_install_wheel_then_wiki_init_renders_a_vault` —
      build a wheel from the source tree, install it into a fresh
      `tmp_path` prefix via `pip install --target=<tmp> <wheel>`,
      then invoke `[sys.executable, "-m", "llm_wiki_kit", "init",
      str(tmp_vault), "--recipe", "family"]` as a subprocess with
      `env={"PYTHONPATH": str(tmp_prefix), **os.environ}`. Assert
      exit 0, assert `<tmp-vault>/.wiki.journal/journal.jsonl`
      exists and parses, assert the journal has at least one
      `vault.init` and one `primitive.install` event. Marked
      `@pytest.mark.slow` and run in a dedicated CI job; not part
      of the default `pytest -q`.

### Documentation

- [x] `docs/architecture/overview.md` §"The Python package" carries
      a one-paragraph note on the bundling convention with a link
      to this spec.
- [x] The TODO comment that precedes `_KIT_ROOT` in `cli.py` is
      removed; the new `_resolve_kit_root()` docstring names this
      spec.

### Status

- [x] Spec `Status: Implemented` when all the above are checked.

## Non-goals

- **Not moving `recipes/`, `core/`, `templates/` into the
  `llm_wiki_kit/` package directory in the source tree.** Catalog
  editors (and the kit's "primitive catalog is the catalog" framing
  in `docs/CHARTER.md`) benefit from top-level visibility. The
  in-wheel relocation is a build-time convention, not a source-
  layout change.
- **Not a `WIKI_KIT_ROOT` env-var override.** An earlier draft of
  this spec introduced one as an "escape hatch for vendored or
  fork-bundled assets." Adversarial review flagged it as scope
  creep — no user has asked for it, B5 doesn't name it, and a new
  env-var contract is forever once shipped. Deferred until a real
  fork-bundler asks. If a future spec introduces such an
  override, it also lands a session-scoped autouse fixture in
  `tests/conftest.py` to keep developer-dotfile env state from
  leaking into tests; this spec does not pre-land that fixture.
- ~~**Not threading `kit_root: Path | None` through
  `cli.main` / `build_parser`** to eliminate the integration tests'
  `monkeypatch.setattr(cli, "_KIT_ROOT", kit)` pattern.~~ Folded into
  this spec — qC8 landed in the same PR as B5 because both touch
  `cli.py:_KIT_ROOT` and splitting them produced no review-time
  benefit. See §What this is for the contract delta and §Acceptance
  criteria → qC8 for the pinned tests.
- **Not touching `doctor.py`'s orphan-territory derivation.**
  `qC10` + `C6` (deriving the kit-owned set from `state.page_writes`)
  ships in its own retro-cleanup PR; this spec leaves `doctor.py`
  alone.
- **Not supporting zipapp / zipped-wheel installs.**
  `importlib.resources` can read them, but the kit's downstream
  callers expect filesystem `Path`s and many `is_dir()` /
  `iterdir()` semantics shift on `zipfile.Path`. If a future user
  needs zipapp support, a follow-up spec converts the asset-
  reading sites to take `Traversable` and updates this spec's
  §Edge cases.
- **Not bundling `examples/`, `docs/`, `tests/`, or `tools/`.** The
  wheel ships runtime assets only. The example vaults are for
  browsing-before-installing and stay in the source repo.
- **Not introducing a new runtime dependency.** Per `AGENTS.md`
  "Runtime dependencies": runtime deps need an ADR. This spec uses
  stdlib-only resolution. The `build` tool added under
  `[project.optional-dependencies].dev` is a dev dependency for
  the wheel-acceptance tests; the kit doesn't import it.
- **Not Windows-specific path handling.** `importlib.resources` and
  hatchling work on Windows; the kit's Windows posture is
  best-effort (the kit's target audience is Mac + Linux). Pinning
  Windows as a first-class target is a `docs/CHARTER.md`-level
  decision and not in scope here.
- **Not bundling the kit-side `.claude/skills/` directory into the
  wheel.** Those are agent-context files for kit contributors, not
  vault-runtime assets. They stay in the source tree and are not
  copied into the wheel.

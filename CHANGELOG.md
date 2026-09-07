# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-09-07

The interface release: one dark theme across every window, a launcher that
opens single applications as readily as whole profiles, a real installer, and
rsync back where it belongs.

### Added

- **Launch a single application by name.** `launcher.apps` indexes what is
  installed — `.desktop` entries, Start Menu shortcuts, macOS app bundles and
  everything on `PATH` — and the launcher lists those matches under the
  profiles. Type `firef`, press Enter, and only Firefox opens. Ad-hoc launches
  are tracked under "Quick launch", so `palaunch status` sees them and
  `palaunch stop` closes them. Also `palaunch app <name>` and the manager's
  new Launch page.
- **The `rsync` step type**, and `palaunch sync --mirror`. `launcher.mirror`
  uses the real rsync when it is installed — the only thing that can reach
  `user@host:/path` — and a built-in walker for local targets when it is not,
  so mirroring works on a bare Windows box too. `--pull` brings a directory
  back, `--dry-run` reports without touching anything, and the Sync page
  offers both.
- **A real setup wizard.** `palaunch install` opens a window that copies the
  sample profiles, writes the JSON Schema, adds a menu entry and registers the
  login item — and `palaunch install --uninstall` removes exactly those,
  leaving your profiles alone. `launcher.install` holds the operations with no
  Tk in sight, so the window, the CLI, the shell scripts and the tests all
  drive the same code.
- **A Windows installer**: `packaging/palaunch.iss` builds
  `palaunch-setup-<version>.exe` (per-user, no administrator prompt, entry in
  "Apps & features", uninstaller), and the release workflow attaches it with a
  checksum and a provenance attestation.
- **A new-profile wizard** in place of the old name prompt: presets, a picker
  over the installed applications, pages to open, processes to close first, a
  side-by-side window layout — all validated through the real loader before
  anything is written. `launcher.scaffold` is the headless half, so
  `palaunch new --app code --url http://localhost:3000` builds the same file.
- **An icon drawn in code.** `launcher.branding` renders the `>_` mark to PNG,
  ICO and SVG in pure Python, for the tray, the window icons, the installers
  and the README. A CI job regenerates `assets/` and fails if the committed
  files have drifted.

### Changed

- **Every window redesigned, dark by default.** `launcher.theme` is now a real
  token set (roles, spacing, a type scale, a font stack resolved against what
  Tk actually has) and `launcher.ui` supplies the widgets Tk lacks — buttons,
  segmented tabs, cards and a scrollbar that belongs to the palette. The
  launcher sizes itself to its results instead of clipping them.
- **The manager is five pages, not seven.** Launch, Profiles, Sync, Activity
  and Settings; secrets and paths moved into Settings, and history and logs
  merged into Activity. `palaunch settings <page>` follows.
- The section list lives in `launcher.panel_model`, so the CLI no longer keeps
  a hand-maintained copy of it.
- The install scripts install the package and then call `palaunch install`,
  which halved them and means a shell script and the window can never register
  different things.
- The tray's *Settings…* entry is now *Manager…*, and the tray icon is the app
  mark rather than a rectangle drawn inline.

### Fixed

- Opening a step in the editor and saving it no longer disables it. `enabled`,
  `detach` and `track` default to true and are usually absent from the file;
  the form rendered them as unticked boxes and then wrote `false` back.
- Tk images are no longer cached across interpreters — the cached window icon
  outlived its window and raised at shutdown.
- Every window is built from one Tk root: the launcher hands over to the
  manager or the editor by hiding rather than closing, and the tests build
  their windows as children of a single root. Asking Tk for a second root took
  the whole process down with a bus error on macOS.
- `theme` now defaults to `dark` rather than `auto`, which on a desktop that
  will not say guessed light.

## [0.3.0] — 2026-08-21

### Added

- **Graphical configuration panel** (`launcher.panel`) covering everything the
  CLI can configure, on seven pages: Settings, Profiles, Secrets, Sync,
  History, Logs and Paths. Long-running actions stream into a console at the
  bottom of the window and never touch a widget from the worker thread.
  - Settings validates each value against the same bounds the panel declares,
    writes only what changed, and preserves keys it does not know about.
  - A setting currently forced by a `PAL_*` environment variable is marked as
    such, because writing the file would otherwise look like it worked.
- **Ways in.** The panel opens from the launcher's new **Settings** row, from
  `Ctrl+,` in the launcher, from the tray's *Settings…* entry, and from
  `palaunch settings [page]` or `palaunch config gui`.
- The HUD now carries `Action` rows alongside profiles, and opens even when no
  profiles exist yet — the Settings row is the way in for a new user.
- `launcher.panel_model`: the panel's field definitions, validation and
  reports, with no Tk involved, so they are covered by tests that run headless.
- A `package` CI job that builds the sdist and wheel, runs `twine check`, and
  installs the wheel into a clean virtualenv on every push.
- `CHANGELOG.md`, `MANIFEST.in` and `py.typed`.

### Fixed

- A `when: {command: …}` condition evaluated with an empty environment failed
  on Windows: handing `CreateProcess` an empty environment block stops the
  child before it starts, which silently turned every such condition false.
  Subprocess environments now go through `config.subprocess_env()`, which
  inherits the parent for an empty mapping and fills in the variables Windows
  cannot start a process without. This was the long-standing CI failure.
- CI ran the Tk tests with no display, so they skipped everywhere. The Linux
  jobs now run under Xvfb, and a step reports when tkinter is unavailable
  rather than letting the coverage vanish quietly.
- `palaunch settings` on a Python built without tkinter now names the package
  to install instead of raising `ImportError`.

### Changed

- CI and release workflows moved to `actions/checkout@v5`,
  `actions/setup-python@v6` and `actions/download-artifact@v5`, clearing the
  Node 20 deprecation warnings; both now run with an isolated `PAL_CONFIG_DIR`
  so a job never writes into a runner's real user config.
- The release workflow also enforces `ruff format --check` and ships `docs/`
  and `CHANGELOG.md` inside the binary archives.
- **README rewritten as a quick start** — install, first profile, how it works,
  in under 200 lines. The former README (architecture, execution model, full
  schema and command reference) moved to
  [`docs/REFERENCE.md`](docs/REFERENCE.md).
- `palaunch edit` and the panel share one "open this path" implementation, so
  both honour the `editor` setting identically.

## [0.2.0]

- Wayland window placement (Sway, Hyprland, KWin), virtual desktops and Spaces.
- Automated releases: PyPI via Trusted Publishing, frozen binaries for four
  targets, Sigstore provenance attestations.
- Test suite and CI across Windows, Linux and macOS on Python 3.10–3.13.

## [0.1.0]

- First release: YAML profiles, the async DAG executor, the HUD picker, the
  system tray, hotkeys, the scheduler, lifecycle commands, the recorder,
  secrets, git sync and the graphical profile editor.

[Unreleased]: https://github.com/MuszKarol/profile-auto-launcher/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v1.0.0
[0.3.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v0.3.0
[0.2.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v0.2.0
[0.1.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v0.1.0

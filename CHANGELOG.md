# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/MuszKarol/profile-auto-launcher/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v0.3.0
[0.2.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v0.2.0
[0.1.0]: https://github.com/MuszKarol/profile-auto-launcher/releases/tag/v0.1.0

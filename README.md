# Profile Auto Launcher

Cross-platform (Windows / Linux / macOS) workflow automation launcher that
replaces the static OS "Startup" folder with **context profiles**. Pick a
profile — "Dev", "Work", "Gaming", "Focus" — and the launcher runs a
declarative graph of apps, URLs, shell commands, scripts, HTTP calls, env
mutations, process kills, file operations and health checks. When you're done,
`palaunch stop` closes everything it opened.

```
┌─ Profile Auto Launcher ──────────────────────────┬───────────────────────────┐
│  > de                                            │ Dev                       │
│    🛠 Dev  ★     Spin up the full dev environment │ code · ide · docker       │
│    💼 Work       Mail, calendar, chat            │ → set [ENVIRONMENT] …     │
│    🎯 Focus      Kill distractions               │ → kill process slack      │
│                                                  │ → spawn: docker start     │
│  ↑↓ navigate  ⏎ run  → preview  ctrl+k stop      │ ⇉ launch /usr/bin/code    │
└──────────────────────────────────────────────────┴───────────────────────────┘
```

---

## Quick start

**Prerequisites:** Python 3.10+, Git.

```bash
# 1. Clone
git clone https://github.com/MuszKarol/profile-auto-launcher.git
cd profile-auto-launcher

# 2. Install (Linux / macOS)
bash scripts/install-linux.sh

# 2. Install (Windows — PowerShell, no admin needed)
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
```

Both scripts install the package, copy the sample profiles and the example
plugin, generate the JSON Schema for editor autocomplete, and register the
launcher to start at login.

Never written a profile before? Let the launcher write the first one:

```bash
palaunch record "My Setup" --duration 120   # open your apps; it watches
palaunch run "My Setup" --dry-run           # check what it captured
```

### Autostart + tray + hotkeys

The launcher's resident mode is `palaunch tray` — it puts an icon in the
system tray (Windows), menu bar (macOS) or AppIndicator area (Linux), listens
for global hotkeys, and evaluates profile `triggers:`.

| OS      | Autostart mechanism (created by the installer)                        |
|---------|------------------------------------------------------------------------|
| Windows | `Profile Auto Launcher.lnk` in the user Startup folder → `palaunchw tray` (no-console binary) |
| Linux   | XDG `~/.config/autostart/*.desktop` (or `--systemd` user unit)          |
| macOS   | LaunchAgent `~/Library/LaunchAgents/com.profile-auto-launcher.tray.plist` |

Once the tray is running:

- **`Alt+Space`** toggles the HUD — press once to open, press again (or `Esc`)
  to close. Change it with `palaunch config set hotkey '<ctrl>+<alt>+p'`.
- **Per-profile hotkeys:** any profile with `hotkey: "<ctrl>+<alt>+d"` runs
  straight from the keyboard, skipping the picker.
- **Tray menu:** every profile by name, a **Stop** submenu listing what is
  currently running, "Reload profiles", "Quit". The menu rebuilds itself when
  you add a YAML file — no restart needed.
- **Triggers** fire on schedule (`at: "09:00"`, `every: 2h`) while the tray runs.
- A profile marked `autostart: true` runs when the tray starts — i.e. right
  after you log in.

> Linux tray note: pystray needs an AppIndicator/GTK backend on some desktops —
> e.g. `sudo apt install gir1.2-appindicator3-0.1` on GNOME (plus the
> AppIndicator extension) or nothing extra on KDE/XFCE.
> Window placement needs `wmctrl` (and `xdotool` for minimising) on X11.

Your profiles live in `~/.config/profile-auto-launcher/profiles/` (Linux),
`~/Library/Application Support/profile-auto-launcher/profiles/` (macOS) or
`%LOCALAPPDATA%\profile-auto-launcher\profiles\` (Windows). Run
`palaunch where` to print every path the launcher uses.

---

## 1. System architecture

```
                      ┌────────────────────────────────────┐
  OS autostart ─────▶ │ launcher.cli  (palaunch tray)      │
                      │  ─ tray icon + hotkeys + scheduler │
                      └───────────┬────────────────────────┘
                                  │ user picks / trigger fires
    Alt+Space / tray / cron ─────▶│
                                  ▼
                      ┌────────────────────────────────────┐
                      │ launcher.hud  (Tk HUD picker)      │
                      └───────────┬────────────────────────┘
                                  │ Profile
                                  ▼
 profiles/*.yaml ──▶ launcher.config ──▶ launcher.executor ──▶ launcher.procs
        │                  │                    │                 (pid registry
        │                  │ interp/conditions  │ asyncio DAG      for `stop`)
        ▼                  ▼                    ▼
   schema.json      {{ vars }} / when:   ┌──────┴───────┬─────────┬──────────┐
   (editor          secrets, probes      │ app command  │ url env │ http     │
    autocomplete)                        │ script kill  │ file    │ plugin   │
                                         │ wait wait_for│ notify  │ profile  │
                                         └──────────────┴─────────┴──────────┘
                                                       │
                              logs/palaunch.log ◀──────┴──────▶ history.jsonl
```

### Module map

| Module                 | Responsibility                                           |
|------------------------|----------------------------------------------------------|
| `launcher.config`      | Discover / parse YAML profiles, per-platform overrides, schema validation |
| `launcher.executor`    | Async DAG step runner, 13 step types, retries, teardown  |
| `launcher.conditions`  | `when:` evaluation (platform, time, process, wifi, battery…) |
| `launcher.interp`      | `{{ … }}` templating over vars, env, time, host, secrets |
| `launcher.secrets`     | OS credential store (Keychain / Credential Manager / libsecret) |
| `launcher.procs`       | Per-profile pid registry, liveness, graceful termination |
| `launcher.windows`     | Window placement: user32, wmctrl/xdotool, AppleScript, virtual desktops |
| `launcher.wayland`     | Wayland placement per compositor (Sway / Hyprland / KWin) |
| `launcher.scheduler`   | Time-based `triggers:` while the tray runs               |
| `launcher.hud`         | Tk HUD picker with live step results — stdlib only       |
| `launcher.editor`      | Graphical profile/step editor                            |
| `launcher.record`      | Watches what you launch, writes it out as a profile      |
| `launcher.tray`        | System tray, self-refreshing menu (pystray/Pillow)       |
| `launcher.hotkey`      | Global + per-profile hotkeys (pynput)                    |
| `launcher.settings`    | `settings.yaml` with `PAL_*` env overrides               |
| `launcher.state`       | Run history, per-step timings, flakiness stats           |
| `launcher.logging_setup` | Rotating log — the only diagnostics `palaunchw` has    |
| `launcher.sync`        | Git sync of the profiles directory                       |
| `launcher.schema`      | JSON Schema generation for editor autocomplete           |
| `launcher.sysprobe`    | Process / Wi-Fi / battery probes (psutil when available) |
| `launcher.notify`      | Native desktop notifications                             |
| `launcher.cli`         | The `palaunch` command surface                           |

### Execution model

Steps form a **dependency graph**. Two kinds of edge exist:

- **Implicit**, derived from layout — the classic rules. A normal step waits
  for everything before it; consecutive `parallel: true` steps form a batch
  that runs concurrently and is joined before the next normal step. `wait` is
  therefore a barrier.
- **Explicit**, via `depends_on: [step-id]`. This is the *only* case where a
  failed dependency skips its dependents; implicit edges never cascade,
  because one broken step must not silently cancel the rest of your desktop.

Other execution properties:

- `max_parallel` (default 8) bounds concurrency across the whole run.
- `retries` + `retry_delay` retry with exponential backoff.
- `on_failure:` runs compensation steps when a step fails.
- Apps are launched **detached** (`CREATE_NEW_PROCESS_GROUP` on Windows,
  `start_new_session=True` on POSIX) so the launcher can exit without killing
  your IDE — while their pids are recorded so `palaunch stop` still can.
- A dependency cycle is reported as a failed step, not a hang.

### OS integration

- **Autostart:** systemd user unit or `.desktop` (Linux), Startup shortcut
  (Windows), LaunchAgent (macOS).
- **Global hotkeys:** `pynput.keyboard.GlobalHotKeys`.
- **System tray:** `pystray.Icon`, menu rebuilt on profile/state change.
- **Secrets:** `keyring` → Keychain / Credential Manager / libsecret.
- **Config directory:** `platformdirs.user_config_dir("profile-auto-launcher")`.
- **Window placement:** one backend per session type. `palaunch where` prints
  which one you are on.

  | Session | Geometry | `workspace:` | Needs |
  |---------|----------|--------------|-------|
  | Windows | `user32` via ctypes | `IVirtualDesktopManager` | — |
  | X11 | `wmctrl` + `xdotool` | `wmctrl` | `wmctrl` (`xdotool` to minimise) |
  | Wayland — Sway | `swaymsg` IPC | `swaymsg` | — |
  | Wayland — Hyprland | `hyprctl` | `hyprctl` | — |
  | Wayland — KWin | KWin script over D-Bus | KWin script | `gdbus`, `kscreen-doctor` |
  | Wayland — GNOME | *not possible* | *not possible* | see below |
  | macOS | AppleScript / System Events | `yabai` | Accessibility permission |

  Wayland gives no protocol for one client to move another's window, so each
  compositor is driven through its own control channel. GNOME/Mutter has none
  that works on release builds — `Shell.Eval` is disabled and placement needs
  a signed extension — so that combination raises a clear error instead of
  failing silently. Run the app under XWayland if you need placement there.

  On Windows, `workspace:` uses the public `MoveWindowToDesktop`, but Windows
  publishes no way to *enumerate* desktops; the ordered GUID list comes from
  Explorer's registry key, which is undocumented and could change in a future
  build. Moves that the owning process refuses are reported, not swallowed.

  On macOS, Spaces have no public API at all — not even for Apple's own
  shortcuts — so `workspace:` drives [yabai](https://github.com/koekeishiya/yabai)
  when it is installed and says so plainly when it is not.

---

## 2. Feature list

### Shipped

**Core**
- [x] YAML profile schema with per-platform command/app/path overrides
- [x] Async DAG executor: implicit ordering, `parallel: true` batches,
      explicit `depends_on`, `max_parallel`
- [x] 13 step types — `app`, `command`, `script`, `url`, `env`, `kill`,
      `wait`, `wait_for`, `profile`, `notify`, `http`, `plugin`, `file`
- [x] Per-step `enabled` / `optional` / `timeout` / `retries` / `retry_delay`
      / `on_failure`
- [x] Conditional steps — `when: {platform, exists, not_exists, env, weekday,
      time_between, process_running, process_not_running, wifi_ssid,
      on_battery, hostname, command}`
- [x] Health checks (`wait_for` — poll tcp/http endpoint or command)
- [x] Profile inheritance (`extends:`) and composition (`type: profile`)
- [x] `{{ … }}` interpolation over profile fields, `vars:`, env, time,
      hostname, user and secrets
- [x] Secret storage via the OS credential store, scrubbed from logs/history
- [x] Plugin system (`type: plugin`, external executables, JSON contract)

**Lifecycle**
- [x] `teardown:` steps + pid tracking → `palaunch stop`
- [x] `palaunch switch` — stop everything else, then run this profile
- [x] `palaunch status` — what's running, and what it last did

**Interface**
- [x] HUD picker — fuzzy filter, live per-step results, step preview,
      light/dark theme following the desktop, running-profile badge
- [x] Graphical profile editor (`palaunch edit <name> --gui`)
- [x] System tray with a self-refreshing menu and a Stop submenu
- [x] Global hotkey + per-profile hotkeys
- [x] Native desktop notifications
- [x] `palaunch record` — writes your first profile by watching you work

**Operations**
- [x] Rotating file log + `palaunch logs [-f]`
- [x] Run history with per-step timings + `palaunch history [--stats]`
- [x] `settings.yaml` + `palaunch config get/set/list`
- [x] Time-based `triggers:` evaluated by the tray
- [x] Git sync of the profiles directory (`palaunch sync`)
- [x] JSON Schema + `# yaml-language-server:` modeline for editor autocomplete
- [x] "Run on boot" installers for systemd / XDG / Startup / LaunchAgent
- [x] Window placement (`window:` on `app`/`command` steps) on Windows, X11,
      Wayland (Sway / Hyprland / KWin) and macOS
- [x] Virtual desktops / Spaces via `workspace:` on every platform that
      exposes a way to do it
- [x] Tests on Windows / Linux / macOS across Python 3.10–3.13
- [x] Tagged releases: PyPI via Trusted Publishing, frozen binaries for four
      targets, Sigstore provenance attestations — see [RELEASING.md](RELEASING.md)

### Known limits

These are platform limits rather than missing work, and each one reports
itself rather than failing quietly:

- **GNOME on Wayland** cannot be driven by an outside program at all. Use
  XWayland or a GNOME extension.
- **Windows virtual desktops** are enumerated from an undocumented registry
  key, because no public API lists them; a future Windows build could move it.
- **macOS Spaces** need `yabai` installed. Apple ships no public API.
- **Platform code signing** is off until certificates are configured — the
  binaries carry Sigstore attestations, which prove origin but do not silence
  Gatekeeper or SmartScreen. `RELEASING.md` covers wiring certificates in.

---

## 3. Configuration schema (YAML)

Editors pick up completion and validation from the generated schema — the
modeline is written into every profile `palaunch new` scaffolds:

```yaml
# yaml-language-server: $schema=file:///home/you/.config/profile-auto-launcher/profile.schema.json
```

### Profile level

```yaml
name: string            # display name (required; defaults to the file stem)
description: string
icon: "🛠"              # shown in the HUD and tray
default: bool           # ★ in `palaunch list`; used when `run` gets no name
autostart: bool         # the tray runs this once at login
hotkey: "<ctrl>+<alt>+d"  # per-profile global shortcut
tags: [code, docker]    # searchable in the HUD
extends: base           # merge another profile file (by file stem)
vars:                   # available as {{ vars.NAME }} everywhere below
  repo: "~/projects/main"
triggers:               # fire the profile on a schedule (tray only)
  - at: "09:00"
    weekday: [mon, tue, wed, thu, fri]
  - every: 2h
    when: {on_battery: false}
steps: [...]            # what the profile does
teardown: [...]         # what `palaunch stop` does before closing processes
```

`extends` merges parent steps first, child scalars win. `default`,
`autostart`, `hotkey` and `name` are never inherited — one parent flag would
otherwise fan out to every derived profile.

### Common step keys

```yaml
- type: app | command | script | url | env | kill | wait | wait_for
      | profile | notify | http | plugin | file
  name: "human label"     # shown in the HUD and logs
  id: docker              # referenced by depends_on
  enabled: true           # false → reported as SKIP, never run
  optional: false         # true → a failure doesn't fail the profile
  parallel: false         # batch with adjacent parallel steps
  depends_on: [docker]    # explicit edges; a failed dependency skips this step
  timeout: 30             # seconds
  retries: 2              # extra attempts after a failure
  retry_delay: 2          # seconds before the first retry, doubling after
  when: {...}             # skip unless every condition holds
  on_failure: [...]       # compensation steps
```

### Step types

```yaml
# Launch an application, detached, optionally placed on screen
- type: app
  path: {windows: "%LOCALAPPDATA%\\...\\Code.exe", linux: /usr/bin/code}
  args: ["{{ vars.repo }}"]
  cwd: "~/projects"
  detach: true            # false → wait for it to exit
  track: true             # false → `palaunch stop` leaves it running
  window:
    monitor: 1            # 1-based; primary first
    position: left-half   # left/right/top/bottom-half, top-left…, center,
                          # full, maximized, fullscreen — or x/y/width/height
    workspace: 3          # virtual desktop / space, 1-based
    match: "Visual Studio"  # title substring, when the pid owns several windows
    focus: true
    timeout: 10           # how long to wait for the window to appear

# Run a command
- type: command
  run: ["git", "pull", "--ff-only"]   # or a string, split like a shell would
  detach: false                       # false → wait and capture the exit code

# Inline shell, without escaping it into an argv list
- type: script
  shell: auto             # auto | bash | sh | zsh | powershell | cmd
  script: |
    git fetch --all --prune
    git pull --ff-only || echo "diverged"

# Open a URL (http/https/mailto/ftp only — file:// and javascript: are refused)
- type: url
  url: https://github.com/pulls

# Mutate the environment for every later step in this run
- type: env
  set: {ENVIRONMENT: development}
  unset: [HTTP_PROXY]

# Close an app by exact process name
- type: kill
  process: {windows: slack.exe, linux: slack, darwin: Slack}

# Fixed delay — also a barrier that joins any in-flight parallel batch
- type: wait
  seconds: 5

# Poll until something is actually ready
- type: wait_for
  url: tcp://localhost:5432   # or http(s)://…
  run: ["docker", "info"]     # …or a command whose exit code decides
  timeout: 60
  interval: 2

# Run another profile as a step
- type: profile
  profile: Focus

# Desktop notification
- type: notify
  title: Dev
  message: "Environment up at {{ time }}"

# HTTP request (webhooks, home automation, status APIs)
- type: http
  method: POST
  url: http://homeassistant.local:8123/api/services/scene/turn_on
  headers: {Authorization: "Bearer {{ secret.ha_token }}"}
  body: {entity_id: scene.evening}
  expect_status: [200, 201]

# External executable with a JSON contract — see plugins/hello_plugin.py
- type: plugin
  plugin: "~/.config/profile-auto-launcher/plugins/hello_plugin.py"
  config: {message: "wrapped up at {{ time }}"}

# File operations — config swapping between contexts
- type: file
  action: copy | symlink | mkdir | remove | write | append
  src: "~/.config/palaunch-configs/focus-settings.json"
  dest: "~/.config/Code/User/settings.json"
  content: "written by {{ profile }}"     # write/append only
```

### Conditions (`when:`)

Every key must hold (AND). A probe that cannot answer — no Wi-Fi tooling, no
battery — makes its condition false: a step guarded by something unverifiable
should not run.

```yaml
when:
  platform: [linux, darwin]
  exists: "~/projects/main/.git"
  not_exists: "/tmp/maintenance.lock"
  env: {ENVIRONMENT: development}
  weekday: [mon, tue, wed, thu, fri]
  time_between: ["09:00", "17:00"]     # wraps past midnight: ["22:00", "06:00"]
  process_running: code
  process_not_running: steam
  wifi_ssid: [HomeNet, OfficeNet]
  on_battery: false
  hostname: [workstation]
  command: ["docker", "info"]          # exit code 0 means true
```

### Interpolation (`{{ … }}`)

```
{{ profile }} {{ profile.icon }}   profile fields
{{ vars.repo }}                    profile-level vars
{{ env.HOME }}                     run-local env (sees earlier `env:` steps)
{{ now }} {{ now:%H-%M }}          timestamps, optional strftime format
{{ date }} {{ time }}
{{ hostname }} {{ user }} {{ home }} {{ platform }}
{{ secret.NAME }}                  OS credential store
```

Unknown placeholders are left verbatim rather than replaced by an empty
string — silently dropping a token from a command line is how you end up
running the wrong argument list. Secret values are replaced by `***` in every
log line, HUD row and history record.

```bash
palaunch secret set ha_token          # prompts without echo
```

### Settings (`settings.yaml`)

```bash
palaunch config list
palaunch config set theme dark        # auto | dark | light
palaunch config set max_parallel 4
palaunch config path
```

| Key | Default | Meaning |
|-----|---------|---------|
| `hotkey` | `<alt>+<space>` | HUD shortcut |
| `theme` | `auto` | HUD/editor palette; `auto` follows the desktop |
| `notifications` | `true` | desktop toasts |
| `log_level` / `log_max_bytes` / `log_backups` | `INFO` / 1 MB / 3 | rotating log |
| `history_limit` | `500` | runs kept in `history.jsonl` |
| `max_parallel` | `8` | concurrent steps (0 = unlimited) |
| `editor` | *(unset)* | overrides `$EDITOR` for `palaunch edit` |
| `scheduler` | `true` | honour `triggers:` while the tray runs |
| `window_management` | `true` | apply `window:` blocks |
| `hud_width` / `hud_height` | 620 / 420 | HUD size |
| `sync_remote` | *(unset)* | git remote for `palaunch sync` |

Any `PAL_*` environment variable of the same name wins over the file, so
`PAL_THEME=light palaunch pick` still works for one-off overrides.

---

## 4. Async launch code

The scheduler is the interesting part: one loop drives both the implicit
ordering and explicit `depends_on` edges.

```python
async def _execute_steps(steps, run, on_result=None):
    deps = _build_dependencies(steps)      # implicit edges + depends_on
    done = [False] * len(steps)
    pending: dict[asyncio.Task, int] = {}
    gate = _semaphore()                    # max_parallel

    while not all(done):
        for i, step in enumerate(steps):           # everything now runnable
            if not done[i] and i not in pending.values():
                if all(done[d] for d in deps[i]):
                    pending[asyncio.create_task(run_indexed(i))] = i

        if not pending:                            # nothing can ever run
            mark_remaining_as_cycle_failures()
            break

        finished, _ = await asyncio.wait(set(pending), return_when=FIRST_COMPLETED)
        for task in finished:
            index = pending.pop(task)
            done[index] = True
            if on_result:
                on_result(task.result())           # streams into the HUD live
```

`_build_dependencies` is what preserves the original semantics:

```python
batch_start = 0
for i, step in enumerate(steps):
    if step.depends_on:                 # explicit wins
        deps.append([index_of[d] for d in step.depends_on])
    elif step.parallel:                 # concurrent with its batch
        deps.append(list(range(batch_start)))
    else:                               # barrier: waits for everything before
        deps.append(list(range(i)))
    if not step.parallel:
        batch_start = i + 1
```

---

## 5. Tech stack rationale

| Choice | Why |
|--------|-----|
| **Python 3.10+** | Ships on macOS/Linux, trivial on Windows; `asyncio` and `subprocess` cover every launch mode we need. |
| **YAML profiles** | Editable by hand, diffable in git, and a JSON Schema gives autocomplete without writing an editor plugin. |
| **Tkinter for the HUD** | Standard library. A launcher you install to save time should not pull a GUI toolkit first. |
| **asyncio, not threads** | Steps are almost entirely I/O — spawning, polling ports, waiting on HTTP. One event loop keeps ordering explicit and cancellation sane. |
| **Optional extras** | pystray, pynput, keyring and psutil are all optional; every integration degrades to a logged message instead of an ImportError. |
| **platformdirs** | The config path differs on all three platforms and guessing it wrong is how profiles end up undiscovered. |
| **No daemon** | The CLI is one-shot; the tray is the only long-lived process, and even it holds no state the files don't. |

---

## 6. Install & run

### Linux

```bash
bash scripts/install-linux.sh              # install + XDG autostart
bash scripts/install-linux.sh --systemd    # …or a systemd user unit
bash scripts/install-linux.sh --no-autostart
bash scripts/uninstall-linux.sh
```

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -NoAutostart
powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
```

### Manual / development

```bash
pip install -e ".[full,dev]"     # everything, including test tooling
pip install -e .                 # core only: CLI + HUD, no tray/hotkey
pytest                           # 200+ tests
ruff check src tests
```

Extras: `tray` (pystray + Pillow), `hotkey` (pynput), `secrets` (keyring),
`probe` (psutil — faster process/Wi-Fi/battery probes), `full`, `dev`.

### From PyPI

```bash
pip install "profile-auto-launcher[full]"
```

### Frozen binary — no Python needed

Every tagged release attaches self-contained binaries for Linux, Windows and
both macOS architectures. Each archive holds `palaunch`, the console-free
`palaunchw`, the sample profiles and the example plugin.

```bash
# verify a download came from this repository's CI
sha256sum -c SHA256SUMS.txt --ignore-missing
gh attestation verify palaunch-0.2.0-linux-x86_64.tar.gz --repo MuszKarol/profile-auto-launcher
```

Building one yourself:

```bash
pip install pyinstaller ".[full]"
pyinstaller packaging/palaunch.spec     # -> dist/palaunch, dist/palaunchw
```

Cutting a release is documented in [RELEASING.md](RELEASING.md).

### Everyday commands

```bash
# running
palaunch list [--triggers]   # discovered profiles, hotkeys, schedules
palaunch run Dev             # run a profile by name (or the default)
palaunch run Dev --dry-run   # describe every step, execute nothing
palaunch run Dev --only "VS Code" --skip docker    # partial runs, for debugging
palaunch last                # re-run whatever you ran last time
palaunch pick                # HUD picker with fuzzy filter and live progress
palaunch tray                # tray + hotkeys + trigger scheduler

# lifecycle
palaunch status              # what's running and what it last did
palaunch stop Dev            # teardown steps, then close tracked processes
palaunch stop --all
palaunch switch Gaming       # stop everything else, then run Gaming

# authoring
palaunch record "My Setup"   # watch what you open, write it out as a profile
palaunch new Gaming [--edit|--gui]
palaunch edit Dev [--gui]    # $EDITOR, or the graphical step editor
palaunch validate [--schema] # lint every profile, non-zero exit on errors
palaunch schema              # (re)write profile.schema.json

# operations
palaunch logs -n 100 -f      # rotating launcher log
palaunch history [--stats]   # past runs; --stats ranks slow and flaky steps
palaunch config set theme dark
palaunch secret set ha_token
palaunch sync --init git@github.com:you/palaunch-profiles.git
palaunch sync -m "add evening profile"
palaunch where               # every path and hotkey the launcher uses
```

For development inside the repo, set `PAL_INCLUDE_CWD=1` to additionally pick
up `./profiles/`. This is opt-in by design — running `palaunch` in an
unfamiliar directory that happens to contain `profiles/*.yaml` would otherwise
execute whatever commands those files declare.

---

## License

MIT

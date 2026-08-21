# Profile Auto Launcher

Replace the OS "Startup" folder with **context profiles**. Pick one — *Dev*,
*Work*, *Gaming*, *Focus* — and the launcher opens the apps, tabs, containers
and terminals that context needs. `palaunch stop` closes them again.

Windows, Linux and macOS. Python 3.10+.

```
┌─ Profile Auto Launcher ──────────────────────────┬───────────────────────────┐
│  > de                                            │ Dev                       │
│    🛠 Dev  ★     Spin up the full dev environment │ code · ide · docker       │
│    💼 Work       Mail, calendar, chat            │ → set [ENVIRONMENT] …     │
│    🎯 Focus      Kill distractions               │ → kill process slack      │
│    ⚙  Settings   Configure the launcher          │ → spawn: docker start     │
│  ↑↓ navigate  ⏎ run  → preview  ctrl+k stop      │ ⇉ launch /usr/bin/code    │
└──────────────────────────────────────────────────┴───────────────────────────┘
```

---

## Install

```bash
pip install "profile-auto-launcher[full]"
```

Or clone and let the installer also register autostart, copy the sample
profiles and generate the editor schema:

```bash
git clone https://github.com/MuszKarol/profile-auto-launcher.git
cd profile-auto-launcher

bash scripts/install-linux.sh                                              # Linux / macOS
powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1       # Windows
```

Prefer no Python at all? Every [release](https://github.com/MuszKarol/profile-auto-launcher/releases)
attaches a self-contained binary for Linux, Windows and both macOS
architectures.

> On Debian/Ubuntu the graphical windows also need `sudo apt install python3-tk`.
> Without it the command line still does everything.

---

## Quick start

**1. Get a profile.** Either let the launcher write your first one by watching
you work:

```bash
palaunch record "My Setup" --duration 120    # open your apps; it watches
```

…or write one by hand — a profile is a YAML file:

```yaml
name: Dev
description: Spin up the full dev environment
icon: "🛠"
hotkey: <ctrl>+<alt>+d

steps:
  - type: app
    name: VS Code
    path:
      windows: "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe"
      linux: /usr/bin/code
    window: {monitor: 1, position: left-half}

  - type: command
    name: docker
    run: ["docker", "compose", "up", "-d"]

  - type: wait_for            # don't open the tab before the server answers
    url: http://localhost:8080

  - type: url
    url: http://localhost:8080

teardown:                     # what `palaunch stop Dev` does
  - type: command
    run: ["docker", "compose", "down"]
```

**2. Check it, then run it.**

```bash
palaunch run "My Setup" --dry-run    # describes every step, executes nothing
palaunch run "My Setup"
palaunch stop "My Setup"             # teardown + close what it opened
```

**3. Make it resident.**

```bash
palaunch tray
```

The tray icon lives in the system tray, menu bar or AppIndicator area. It
listens for **`Alt+Space`** — which opens the launcher — runs any profile with
its own `hotkey:`, and fires profiles that carry a schedule. The installers
register it to start at login for you.

---

## How it works

Profiles are YAML files in your config directory (`palaunch where` prints the
exact path). Each is a list of **steps**, and the steps form a graph rather
than a script: a step waits for the ones before it, consecutive
`parallel: true` steps run together, and `depends_on:` wires an explicit edge.
So a launch takes as long as its slowest branch, not the sum of its parts.

There are 13 step types — launch an app, run a command or an inline script,
open a URL, set environment variables, kill a process, wait, poll until a
service is actually ready, run another profile, notify, call an HTTP endpoint,
run a plugin, or copy/link/write a file. Any step can be conditional
(`when: {platform: linux, on_battery: false}`), optional, retried, or given a
timeout. Values interpolate: `{{ vars.project }}`, `{{ env.HOME }}`,
`{{ secret.api_token }}` from your OS credential store.

Everything the launcher started is tracked, which is what makes `palaunch stop`
and `palaunch switch Gaming` work.

### The launcher window

`Alt+Space` (or `palaunch pick`) opens the picker: type to filter, `↑↓` to
move, `→` to preview the steps, `Enter` to run — and watch each step report as
it finishes.

The last row is **Settings**, which opens the configuration panel — the same
window as `palaunch settings` and the tray's *Settings…*, and everything the
CLI can configure:

| Page | What it does | CLI equivalent |
|------|--------------|----------------|
| Settings | Every option in `settings.yaml`, validated as you save | `palaunch config set` |
| Profiles | Run, dry-run, stop, switch, create, edit, record, validate | `run` / `stop` / `new` / `edit` |
| Secrets  | Add and remove credential-store entries | `palaunch secret` |
| Sync     | Initialise, inspect and push the profiles git repo | `palaunch sync` |
| History  | Past runs and per-step timing and failure rates | `palaunch history` |
| Logs     | Tail of the rotating log | `palaunch logs` |
| Paths    | Every path and hotkey; writes the editor JSON Schema | `palaunch where` |

### Commands worth knowing

```bash
palaunch list                # what profiles you have
palaunch run Dev             # …or the default profile, if you name none
palaunch last                # re-run whatever you ran last
palaunch status              # what's running right now
palaunch switch Gaming       # stop everything else, then run this
palaunch settings            # the configuration panel
palaunch where               # every path and hotkey the launcher uses
```

`palaunch --help` lists the rest.

---

## Documentation

- **[docs/REFERENCE.md](docs/REFERENCE.md)** — architecture, the execution
  model, every step type, condition, placeholder and setting, and the full
  command list.
- **[RELEASING.md](RELEASING.md)** — how a version is cut.
- **[CHANGELOG.md](CHANGELOG.md)** — what changed.

## Contributing

```bash
pip install -e ".[full,dev]"
pytest                       # GUI tests skip without a display; use xvfb-run on Linux
ruff check src tests && ruff format --check src tests
```

Set `PAL_INCLUDE_CWD=1` to also pick up `./profiles/` from the repository.
It is opt-in on purpose: running `palaunch` in an unfamiliar directory that
happens to contain `profiles/*.yaml` would otherwise execute whatever those
files declare.

## License

MIT — see [LICENSE](LICENSE).

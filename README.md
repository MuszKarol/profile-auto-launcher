<div align="center">

<img src="assets/icon.svg" width="88" alt="">

# Profile Auto Launcher

**One window opens your whole working context** — the apps, the tabs, the
containers, the terminals. Pick *Dev*, *Work* or *Gaming*; `palaunch stop`
closes it all again.

Windows, Linux and macOS. Python 3.10+.

</div>

```
┌──────────────────────────────────────────────────────────────────────────┐
│  >_  Search profiles and apps                                            │
│                                                                          │
│  PROFILES                                                                │
│ ▌   Dev           Spin up the full dev environment            4 steps    │
│     Focus         Kill distractions, start the timer          2 steps    │
│     Work          Mail, calendar, chat             default    2 steps    │
│  APPLICATIONS                                                            │
│     Firefox       Web browser                                  launch    │
│  LAUNCHER                                                                │
│     Settings      Open the manager                       open manager    │
│                                                                          │
│  ↑↓ move   ⏎ run   → preview   ^E edit   ^K stop   ^, manager   esc close│
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Install

**Windows** — download `palaunch-setup-<version>.exe` from the
[latest release](https://github.com/MuszKarol/profile-auto-launcher/releases)
and run it. It installs for your user only, so there is no administrator
prompt, and it can start the launcher when you sign in.

**Everything else** — install the package and run the setup window:

```bash
pip install "profile-auto-launcher[full]"
palaunch install
```

That window copies the sample profiles, writes the editor schema, adds a menu
entry and registers the login item. `palaunch install --cli` does the same
thing without a window, and `palaunch install --uninstall` undoes it — your
profiles are never touched.

Prefer no Python at all? Every [release](https://github.com/MuszKarol/profile-auto-launcher/releases)
attaches a self-contained binary for Linux, Windows and both macOS
architectures; run `./palaunch install` from the unpacked archive.

> On Debian/Ubuntu the windows also need `sudo apt install python3-tk`.
> Without it the command line still does everything.

---

## Quick start

**1. Make a profile.** The wizard is the short way — it lists what you have
installed and writes the YAML for you:

```bash
palaunch new "My Setup" --gui
```

The command line does it in one line, and so does a recording of you working:

```bash
palaunch new "My Setup" --app firefox --app code --url https://localhost:3000
palaunch record "My Setup" --duration 120     # open your apps; it watches
```

A profile is a YAML file, so you can also just write one:

```yaml
name: Dev
description: Spin up the full dev environment
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

  - type: rsync               # mirror today's notes to the NAS
    src: ~/notes
    dest: nas:/volume1/notes
    delete: true

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
its own `hotkey:`, and fires profiles that carry a schedule. Setup registers it
to start at login for you.

---

## The two windows

### The launcher — `Alt+Space`, or `palaunch pick`

Type to filter, `↑↓` to move, `→` to preview the steps, `Enter` to run — and
watch each step report as it finishes.

The whole interface is monochrome on purpose: one accent (white), no colour
coding, no emoji, and outline icons only on the navigation and the tool
headers. Hierarchy comes from weight and space, which is what keeps a window
this dense readable.

It searches **profiles and installed applications together**, so a context you
have never bothered to write a profile for is still one keystroke away: type
`firef`, press Enter, and only Firefox opens. Everything started that way is
tracked, so `palaunch status` sees it and `palaunch stop "Quick launch"`
closes it.

### The manager — `palaunch settings`, `Ctrl+,`, or the tray

Five pages, everything the CLI can do:

| Page | What it does | CLI equivalent |
|------|--------------|----------------|
| Launch   | Run a profile, or one app by name; stop what is running | `run` / `app` / `stop` |
| Profiles | Create, edit, record, validate, switch | `new` / `edit` / `record` |
| Sync     | Git repo **and** rsync mirror of the profiles directory | `palaunch sync [--mirror]` |
| Activity | Past runs, per-step timings and failure rates, the log | `history` / `logs` |
| Settings | Every option, the credential store, the paths, setup | `config set` / `secret` / `where` |

---

## How it works

Profiles are YAML files in your config directory (`palaunch where` prints the
exact path). Each is a list of **steps**, and the steps form a graph rather
than a script: a step waits for the ones before it, consecutive
`parallel: true` steps run together, and `depends_on:` wires an explicit edge.
So a launch takes as long as its slowest branch, not the sum of its parts.

There are 14 step types — launch an app, run a command or an inline script,
open a URL, set environment variables, kill a process, wait, poll until a
service is actually ready, mirror a directory with rsync, run another profile,
notify, call an HTTP endpoint, run a plugin, or copy/link/write a file. Any
step can be conditional (`when: {platform: linux, on_battery: false}`),
optional, retried, or given a timeout. Values interpolate:
`{{ vars.project }}`, `{{ env.HOME }}`, `{{ secret.api_token }}` from your OS
credential store.

Everything the launcher started is tracked, which is what makes `palaunch stop`
and `palaunch switch Gaming` work.

### Commands worth knowing

```bash
palaunch list                # what profiles you have
palaunch run Dev             # …or the default profile, if you name none
palaunch app firefox         # just one program, no profile needed
palaunch last                # re-run whatever you ran last
palaunch status              # what's running right now
palaunch switch Gaming       # stop everything else, then run this
palaunch sync --mirror       # rsync the profiles to your NAS or USB stick
palaunch settings            # the manager window
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
python -m launcher.branding assets     # regenerate the icon files
```

Set `PAL_INCLUDE_CWD=1` to also pick up `./profiles/` from the repository.
It is opt-in on purpose: running `palaunch` in an unfamiliar directory that
happens to contain `profiles/*.yaml` would otherwise execute whatever those
files declare.

## License

MIT — see [LICENSE](LICENSE).

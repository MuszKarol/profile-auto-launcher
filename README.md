# Profile Auto Launcher

Cross-platform (Windows / Linux / macOS) workflow automation launcher that
replaces the static OS "Startup" folder with **context profiles**. Pick a
profile at boot — "Dev", "Work", "Gaming", "Focus" — and the launcher runs a
declarative sequence of apps, URLs, shell commands, env mutations, process
kills, and timed delays.

```
┌─ Profile Auto Launcher ─────────────────────────────────┐
│  > de                                                    │
│    Dev  ★      —   Spin up the full dev environment      │
│    Work        —   Meetings, email, chat — no IDE        │
│    Focus       —   Silence notifications …               │
│  ↑↓ navigate · Enter run · Esc cancel                    │
└──────────────────────────────────────────────────────────┘
```

---

## 1. System architecture

```
                      ┌────────────────────────────────────┐
  OS autostart ─────▶ │ launcher.cli  (palaunch tray)      │
                      │  ─ starts tray icon + hotkey       │
                      └───────────┬────────────────────────┘
                                  │ user picks profile
    Alt+Space / tray menu ───────▶│
                                  ▼
                      ┌────────────────────────────────────┐
                      │ launcher.hud  (Tk HUD picker)      │
                      └───────────┬────────────────────────┘
                                  │ Profile
                                  ▼
 ~/.config/pal/profiles/*.yaml ─▶ launcher.config  ──▶  launcher.executor
                                                        │
                                                        │ asyncio
                                                        ▼
                         ┌──────────┬──────────┬──────────┬──────────┐
                         │ apps     │ commands │ urls     │ env/kill │
                         │ (detached│ (async   │ (webbr.) │ (pkill/  │
                         │  spawn)  │  exec)   │          │ taskkill)│
                         └──────────┴──────────┴──────────┴──────────┘
```

### Module map

| Module                 | Responsibility                                           |
|------------------------|----------------------------------------------------------|
| `launcher.config`      | Discover / parse YAML profiles, per-platform overrides   |
| `launcher.executor`    | Async step runner (sequential by default, parallel opt-in)|
| `launcher.hud`         | Tk-based HUD picker — stdlib only, no extra deps         |
| `launcher.tray`        | Optional system tray (pystray/Pillow)                    |
| `launcher.hotkey`      | Optional global hotkey listener (pynput)                 |
| `launcher.cli`         | `palaunch list | run | pick | tray | where`              |

### Execution model

- Steps in a profile are ordered and **sequential by default**.
- Consecutive steps marked `parallel: true` are batched and launched
  **concurrently** via `asyncio.gather`. The batch is awaited before the next
  non-parallel step or `wait`.
- `wait` drains any in-flight batch, sleeps, and continues. This is what
  implements *"wait 5s after launching Docker before starting the IDE"*.
- Apps are launched **detached** (`CREATE_NEW_PROCESS_GROUP` on Windows,
  `start_new_session=True` on POSIX) so the launcher can exit without killing
  your IDE.

### OS integration

- **Autostart**
  - Linux: systemd user unit, or a `.desktop` file in `~/.config/autostart/`
  - Windows: Startup folder shortcut, or a `Run` registry entry
- **Global hotkey:** `pynput.keyboard.GlobalHotKeys` — default `Alt+Space`.
- **System tray:** `pystray.Icon` with a menu of profiles + "Open Launcher…".
- **Config directory:** `platformdirs.user_config_dir("profile-auto-launcher")`
  (`~/.config/profile-auto-launcher/` on Linux, `%APPDATA%\…` on Windows).

---

## 2. MVP feature list (prioritised)

### P0 — shipped in this repo
- [x] YAML profile schema with per-platform command/app/path overrides
- [x] Async executor: apps, URLs, commands, env mutations, process kills, waits
- [x] Sequential-by-default + `parallel: true` batches
- [x] Detached app spawns (launcher can exit safely)
- [x] HUD picker (Tk, stdlib) — keyboard-first, filter-as-you-type
- [x] CLI: `list / run / pick / tray / where`
- [x] Graceful degradation when tray/hotkey deps are missing

### P1 — next
- [ ] System tray with profile menu (stub shipped; pystray optional dep)
- [ ] Global hotkey `Alt+Space` opens HUD (stub shipped; pynput optional dep)
- [ ] "Run on boot" installer for systemd / Windows Startup
- [ ] Per-step success/failure overlay (toast-style)
- [ ] Profile editor UI

### P2 — later
- [ ] Conditional steps (`when: profile == 'dev' and $(git status) == clean`)
- [ ] Retry/timeout per step
- [ ] Health checks ("wait until `curl localhost:5432` succeeds")
- [ ] Secret storage (macOS Keychain / Win Credential Manager / libsecret)
- [ ] Plugin system (`type: plugin`, external executables)
- [ ] Cloud sync of profiles across machines

---

## 3. Configuration schema (YAML)

```yaml
name: string           # profile display name (required)
description: string    # shown in the HUD
default: bool          # run this if no name is passed to `palaunch run`

steps:                 # ordered list
  - type: app | command | url | env | kill | wait
    name: string               # optional label for logs
    parallel: bool             # batch with adjacent parallel steps
    # type=app / command —----------------------------------------
    path: string | {windows,linux,darwin}   # app only
    run:  list | string | {windows,linux,darwin}   # command only
    args: [string]                          # app only
    cwd:  string
    detach: bool                # true = launch-and-forget (default)
    # type=url —--------------------------------------------------
    url: string
    # type=wait —-------------------------------------------------
    seconds: float
    # type=env —--------------------------------------------------
    set:   { KEY: value }
    unset: [KEY]
    # type=kill —-------------------------------------------------
    process: string | {windows,linux,darwin}
```

A fully annotated **Dev Mode** profile lives in [`profiles/dev.yaml`](profiles/dev.yaml):

```yaml
name: Dev
description: Spin up the full dev environment (Docker, IDE, repos, dashboards)
default: true
steps:
  - type: env
    set: { ENVIRONMENT: development, PAL_SESSION: dev }
    unset: [HTTP_PROXY]

  - type: kill
    name: close Slack
    process: { windows: slack.exe, linux: slack, darwin: Slack }

  - type: command
    name: start docker
    run:
      windows: ["powershell", "-NoProfile", "-Command", "Start-Process 'Docker Desktop'"]
      linux:  ["systemctl", "--user", "start", "docker-desktop"]
      darwin: ["open", "-a", "Docker"]

  - type: wait
    seconds: 5

  - type: app
    name: VS Code
    parallel: true
    path:
      windows: "%LOCALAPPDATA%\\Programs\\Microsoft VS Code\\Code.exe"
      linux:   /usr/bin/code
    args: ["~/projects/main"]

  - type: app
    name: Terminal
    parallel: true
    path: { windows: wt.exe, linux: /usr/bin/gnome-terminal }

  - type: url
    url: https://github.com/pulls/review-requested

  - type: command
    name: pull latest main
    detach: false
    cwd: "~/projects/main"
    run: ["git", "pull", "--ff-only", "origin", "main"]
```

---

## 4. Async launch code snippet

Heart of `launcher/executor.py` — sequential by default, adjacent
`parallel: true` steps batched, `wait` blocks the queue:

```python
async def run_profile_async(profile: Profile, on_result=None) -> list[StepResult]:
    env = os.environ.copy()
    results: list[StepResult] = []
    batch: list[asyncio.Task[StepResult]] = []

    async def drain() -> None:
        if not batch:
            return
        for res in await asyncio.gather(*batch):
            results.append(res)
            if on_result:
                on_result(res)
        batch.clear()

    for step in profile.steps:
        if step.type == "wait":
            await drain()
            await asyncio.sleep(step.seconds)
            continue
        if step.parallel:
            batch.append(asyncio.create_task(_run_step(step, env)))
            continue
        await drain()
        results.append(await _run_step(step, env))
    await drain()
    return results
```

Detached spawn (so your IDE survives the launcher exiting):

```python
def _spawn(argv, *, env, cwd, detach):
    kwargs = {"env": env, "cwd": cwd, "close_fds": True}
    if detach:
        if PLATFORM == "windows":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        kwargs.update(stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    subprocess.Popen(argv, **kwargs)
```

---

## 5. Tech stack rationale

| Option                         | Pros                                                  | Cons                                            |
|--------------------------------|-------------------------------------------------------|-------------------------------------------------|
| **Python 3.10+ (chosen)**      | Stdlib tk for HUD, tiny surface, fast iteration       | Ships Python runtime                             |
| Tauri (Rust + web frontend)    | 5–10 MB binary, native perf, great for production UI  | Slower to iterate, Rust ramp-up                  |
| Electron (Node + web frontend) | Ubiquitous tooling, Raycast-style UIs easy            | 100 MB+ footprint, heavy RAM                     |

Python was chosen for the MVP because the *logic* — YAML loading, async
process orchestration, cross-platform spawning — is trivial in the stdlib, and
the HUD only needs a few hundred lines of Tk. Once the schema and execution
semantics stabilise, re-implementing the runtime in **Tauri** (Rust core +
Svelte/React HUD) is a straightforward port: keep the YAML schema, re-write
`executor.py` in `tokio`, and render a Raycast-style command palette in the
webview.

---

## 6. Install & run

```bash
# from the repo root
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate
pip install -e .              # core
pip install -e '.[full]'      # + pystray + pynput for tray & hotkey

palaunch list                 # see discovered profiles
palaunch run Dev              # run "Dev" profile
palaunch pick                 # HUD picker
palaunch tray                 # system tray + global hotkey
palaunch where                # print config paths
```

The launcher looks for profiles in `~/.config/profile-auto-launcher/profiles/`
first, then falls back to `./profiles/` (handy for development). Drop the
bundled `profiles/*.yaml` into the user config dir to make them persist.

### Autostart (Linux)

```ini
# ~/.config/autostart/profile-auto-launcher.desktop
[Desktop Entry]
Type=Application
Exec=palaunch tray
Name=Profile Auto Launcher
X-GNOME-Autostart-enabled=true
```

### Autostart (Windows)

Create a shortcut to `palaunch.exe tray` in
`shell:startup` (`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`).

---

## License

MIT

"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from launcher.config import (
    Profile,
    config_dir,
    default_profile,
    discover_profiles,
    find_profile,
    load_profile,
    profile_files,
    profiles_dir,
)
from launcher.executor import dry_run_profile, format_result, run_profile
from launcher.notify import notify

# Mirrors launcher.panel.SECTIONS. Duplicated on purpose: importing the panel
# would drag tkinter into every `palaunch --help`, and a test pins them equal.
PANEL_SECTIONS = ("Settings", "Profiles", "Secrets", "Sync", "History", "Logs", "Paths")

_NEW_PROFILE_TEMPLATE = """\
{modeline}
name: {name}
description: Describe what this profile sets up
icon: "\\U0001F680"

# vars are available as {{{{ vars.NAME }}}} anywhere below
vars:
  session: {slug}

steps:
  - type: env
    set:
      PAL_SESSION: "{{{{ vars.session }}}}"

  - type: url
    name: example tab
    url: https://example.com

  # - type: app
  #   name: VS Code
  #   path:
  #     windows: "%LOCALAPPDATA%\\\\Programs\\\\Microsoft VS Code\\\\Code.exe"
  #     linux:   /usr/bin/code
  #   window:
  #     monitor: 1
  #     position: left-half

  # - type: command
  #   name: pull latest
  #   detach: false
  #   timeout: 30        # seconds; kill if it runs longer
  #   retries: 1         # retry once on failure
  #   retry_delay: 2     # wait 2s, then 4s, then 8s…
  #   optional: true     # failure doesn't fail the profile
  #   run: ["git", "pull", "--ff-only"]

# teardown runs on `palaunch stop {name}`
# teardown:
#   - type: notify
#     message: "{name} closed"
"""


# ── helpers ──────────────────────────────────────────────────────────────


def _execute(
    profile: Profile,
    dry_run: bool = False,
    only: list[str] | None = None,
    skip: list[str] | None = None,
) -> int:
    if dry_run:
        print(f"▶ dry run of profile: {profile.name}")
        for res in dry_run_profile(profile):
            print(format_result(res))
        return 0
    print(f"▶ running profile: {profile.name}")
    results = run_profile(
        profile,
        on_result=lambda r: print(format_result(r)),
        only=only or [],
        skip=skip or [],
    )
    failed = sum(1 for r in results if r.counts_as_failure)
    ok = len(results) - failed
    if failed == 0:
        notify(f"{profile.name} — profile finished", f"✓ all {ok} steps succeeded")
    else:
        notify(f"{profile.name} — profile finished with errors", f"{ok} ok, {failed} failed")
    return 0 if failed == 0 else 1


def _resolve(name: str | None) -> Profile | None:
    return find_profile(name) if name else default_profile(discover_profiles())


def _require(name: str | None) -> Profile | None:
    profile = _resolve(name)
    if profile is None:
        print(f"Profile '{name or 'default'}' not found.", file=sys.stderr)
    return profile


def _open_in_editor(path: Path) -> int:
    from launcher.panel_model import open_path

    try:
        open_path(path)
    except OSError as exc:
        print(f"Could not open editor: {exc}", file=sys.stderr)
        return 1
    return 0


# ── commands ─────────────────────────────────────────────────────────────


def _cmd_list(args: argparse.Namespace) -> int:
    from launcher import procs, scheduler

    profiles = discover_profiles()
    if not profiles:
        print(f"No profiles found. Drop YAML files in {profiles_dir()}.")
        return 1
    active = procs.active_profiles()
    for p in profiles:
        marker = "★" if p.default else " "
        icon = f"{p.icon} " if p.icon else ""
        steps = f"({len(p.steps)} steps)"
        running = f"  ● {len(active[p.name])} running" if p.name in active else ""
        print(f" {marker} {icon}{p.name:<16} {steps:<12} {p.description}{running}")
        if p.hotkey:
            print(f"      hotkey: {p.hotkey}")
        if args.triggers:
            for line in scheduler.describe(p):
                print(f"      trigger: {line}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    profile = _require(args.name)
    if profile is None:
        return 2
    return _execute(profile, args.dry_run, args.only, args.skip)


def _cmd_last(args: argparse.Namespace) -> int:
    from launcher.state import load_state

    name = load_state().get("last_profile")
    if not name:
        print("No recorded runs yet — run a profile first.", file=sys.stderr)
        return 1
    profile = find_profile(name)
    if profile is None:
        print(f"Last-run profile '{name}' no longer exists.", file=sys.stderr)
        return 2
    return _execute(profile, dry_run=args.dry_run)


def _cmd_stop(args: argparse.Namespace) -> int:
    from launcher import procs
    from launcher.executor import stop_profile

    if args.all:
        names = list(procs.active_profiles())
        if not names:
            print("Nothing is running.")
            return 0
    else:
        profile = _require(args.name)
        if profile is None:
            return 2
        names = [profile.name]

    exit_code = 0
    for name in names:
        profile = find_profile(name)
        if profile is None:
            stopped, stubborn = procs.stop_profile(name)
            print(f"■ {name}: {stopped} process(es) closed, {stubborn} survived")
            continue
        results, stopped, stubborn = stop_profile(
            profile, on_result=lambda r: print(format_result(r))
        )
        print(f"■ {name}: {stopped} process(es) closed, {stubborn} survived")
        if stubborn:
            exit_code = 1
        notify(f"{name} — stopped", f"{stopped} process(es) closed")
    return exit_code


def _cmd_switch(args: argparse.Namespace) -> int:
    from launcher import procs
    from launcher.executor import stop_profile

    target = _require(args.name)
    if target is None:
        return 2
    for name in list(procs.active_profiles()):
        if name.lower() == target.name.lower():
            continue
        running = find_profile(name)
        if running is None:
            procs.stop_profile(name)
            print(f"■ stopped {name}")
            continue
        _results, stopped, _stubborn = stop_profile(running)
        print(f"■ stopped {name} ({stopped} process(es))")
    return _execute(target)


def _cmd_status(_args: argparse.Namespace) -> int:
    from launcher import procs
    from launcher.state import load_history, load_state

    procs.prune()
    active = procs.active_profiles()
    if not active:
        print("No profile is currently running.")
    for name, running in active.items():
        print(f"● {name} — {len(running)} process(es)")
        for proc in running:
            print(f"    {proc.pid:>7}  {proc.label or proc.cmd[:50]}")
    state = load_state()
    if state.get("last_profile"):
        print(f"\nlast run: {state['last_profile']} at {state.get('last_run', '?')}")
    recent = load_history(limit=1)
    if recent:
        entry = recent[0]
        print(
            f"last result: {entry['ok']} ok, {entry['failed']} failed, "
            f"{entry['skipped']} skipped in {entry['duration']}s"
        )
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    paths = profile_files()
    if not paths:
        print(f"No profile files found in {profiles_dir()}.")
        return 1
    errors = 0
    for path in paths:
        try:
            prof = load_profile(path)
        except Exception as exc:  # yaml/schema errors of any shape
            errors += 1
            print(f" ✗ {path.name}: {exc}")
            continue
        extras = []
        if prof.triggers:
            extras.append(f"{len(prof.triggers)} trigger(s)")
        if prof.teardown:
            extras.append(f"{len(prof.teardown)} teardown step(s)")
        suffix = f" [{', '.join(extras)}]" if extras else ""
        print(f" ✓ {path.name}: '{prof.name}' — {len(prof.steps)} steps{suffix}")
    print(f"{len(paths) - errors}/{len(paths)} profiles valid.")
    if args.schema:
        from launcher import schema

        print(f"Schema written to {schema.write()}")
    return 0 if errors == 0 else 1


def _cmd_new(args: argparse.Namespace) -> int:
    from launcher import schema

    directory = profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    slug = args.name.lower().replace(" ", "-")
    path = directory / f"{slug}.yaml"
    if path.exists():
        print(f"Profile file already exists: {path}", file=sys.stderr)
        return 1
    schema_file = schema.write()
    path.write_text(
        _NEW_PROFILE_TEMPLATE.format(
            name=args.name, slug=slug, modeline=schema.modeline(schema_file)
        ),
        encoding="utf-8",
    )
    print(f"Created {path}")
    if args.gui:
        from launcher.editor import open_editor

        open_editor(path)
        return 0
    if args.edit:
        return _open_in_editor(path)
    print(f"Edit it, then try: palaunch run {args.name} --dry-run")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    if not args.name:
        if args.gui:
            print("--gui needs a profile name.", file=sys.stderr)
            return 2
        return _open_in_editor(profiles_dir())
    profile = find_profile(args.name)
    if profile is None or profile.source_path is None:
        print(f"Profile '{args.name}' not found.", file=sys.stderr)
        return 2
    if args.gui:
        from launcher.editor import edit_profile

        edit_profile(profile)
        return 0
    return _open_in_editor(profile.source_path)


def _cmd_pick(_args: argparse.Namespace) -> int:
    from launcher.hud import pick_and_run

    # Opens even with nothing to pick: the HUD's Settings row is the way in
    # for someone who has not written a profile yet.
    return pick_and_run(discover_profiles())


def _cmd_tray(args: argparse.Namespace) -> int:
    from launcher import settings
    from launcher.executor import stop_profile
    from launcher.hotkey import run_hotkey
    from launcher.hud import toggle_pick_and_run
    from launcher.logging_setup import setup
    from launcher.scheduler import Scheduler
    from launcher.tray import run_tray

    setup(console=True)
    profiles = discover_profiles()
    if not profiles:
        print("No profiles found — tray has nothing to launch.", file=sys.stderr)
        return 1

    def execute(p: Profile) -> None:
        results = run_profile(p, on_result=lambda r: print(format_result(r)))
        failed = sum(1 for r in results if r.counts_as_failure)
        print(f"✓ {p.name}: {len(results) - failed} ok, {failed} failed")
        status = "✓ all steps succeeded" if failed == 0 else f"✗ {failed} step(s) failed"
        notify(f"{p.name} — profile finished", status)

    def stop(p: Profile) -> None:
        _results, stopped, stubborn = stop_profile(p)
        notify(f"{p.name} — stopped", f"{stopped} process(es) closed, {stubborn} survived")

    def open_hud() -> None:
        # hotkey toggles: a second press closes an already-open HUD
        toggle_pick_and_run(discover_profiles())

    bindings = {p.hotkey: (lambda prof: lambda: execute(prof))(p) for p in profiles if p.hotkey}
    run_hotkey(open_hud, bindings)

    scheduler = None
    if settings.load().scheduler and not args.no_scheduler:
        scheduler = Scheduler(discover_profiles, execute)
        scheduler.start()

    auto = next((p for p in profiles if p.autostart), None)
    if auto is not None:
        print(f"▶ autostart profile: {auto.name}")
        import threading

        threading.Thread(target=execute, args=(auto,), daemon=True).start()

    run_tray(
        discover_profiles,
        execute,
        open_hud,
        on_stop=stop,
        on_quit=(scheduler.stop if scheduler else None),
    )
    return 0


def _cmd_where(_args: argparse.Namespace) -> int:
    from launcher import hotkey, schema
    from launcher.config import settings_path
    from launcher.logging_setup import log_path
    from launcher.state import history_path

    print(f"config dir:   {config_dir()}")
    print(f"profiles dir: {profiles_dir()}")
    print(f"settings:     {settings_path()}")
    print(f"log file:     {log_path()}")
    print(f"history:      {history_path()}")
    print(f"schema:       {schema.schema_path()}")

    from launcher import windows

    print(f"window backend: {windows.backend_name()}")
    print("\nhotkeys:")
    for line in hotkey.describe(discover_profiles()):
        print(f"  {line}")
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    from launcher.logging_setup import log_path, tail

    if args.path:
        print(log_path())
        return 0
    if not log_path().is_file():
        print("No log file yet — run a profile first.", file=sys.stderr)
        return 1
    for line in tail(args.lines):
        print(line)
    if not args.follow:
        return 0
    with log_path().open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, os.SEEK_END)
        try:
            while True:
                line = fh.readline()
                if line:
                    print(line.rstrip())
                else:
                    time.sleep(0.4)
        except KeyboardInterrupt:
            return 0


def _cmd_history(args: argparse.Namespace) -> int:
    from launcher.state import clear_history, load_history, step_stats

    if args.clear:
        clear_history()
        print("History cleared.")
        return 0
    if args.stats:
        rows = step_stats(profile=args.profile)
        if not rows:
            print("No history yet.")
            return 1
        print(f"{'profile':<14}{'step':<26}{'runs':>5}{'fail%':>7}{'avg s':>8}{'max s':>8}")
        for row in rows[: args.limit]:
            print(
                f"{row['profile'][:13]:<14}{row['step'][:25]:<26}{row['runs']:>5}"
                f"{row['failure_rate'] * 100:>6.0f}%{row['avg_duration']:>8.2f}"
                f"{row['max_duration']:>8.2f}"
            )
        return 0

    records = load_history(limit=args.limit, profile=args.profile)
    if not records:
        print("No history yet.")
        return 1
    for record in records:
        mark = "✓" if record["failed"] == 0 else "✗"
        kind = "" if record.get("kind") == "run" else f" [{record.get('kind')}]"
        print(
            f"{mark} {record['ts']}  {record['profile']:<14}{kind} "
            f"{record['ok']} ok, {record['failed']} failed, "
            f"{record['skipped']} skipped  ({record['duration']}s)"
        )
        if args.verbose:
            for step in record.get("steps", []):
                glyph = "○" if step["skipped"] else ("✓" if step["ok"] else "✗")
                print(f"      {glyph} {step['name']:<24} {step['detail'][:70]}")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    from launcher import record

    name = args.name
    path = (
        Path(args.output)
        if args.output
        else profiles_dir() / f"{name.lower().replace(' ', '-')}.yaml"
    )
    if path.exists() and not args.force:
        print(f"{path} already exists — pass --force to overwrite.", file=sys.stderr)
        return 1

    print(f"● Recording for {args.duration:.0f}s — open the apps you want in '{name}'.")
    print("  Ctrl+C stops early and keeps what was captured.\n")

    def tick(remaining: float, found: int) -> None:
        print(f"\r  {remaining:5.0f}s left — {found} app(s) captured", end="", flush=True)

    candidates = record.observe(duration=args.duration, on_tick=tick)
    print()
    if not candidates:
        print("Nothing new was captured. Start the apps after the recording begins.")
    for candidate in candidates:
        print(f"  + {candidate.name}  ({candidate.exe})")
    record.write_profile(name, candidates, path)
    print(f"\nWrote {path}")
    print(f"Review it, then: palaunch run {name} --dry-run")
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    from launcher import settings
    from launcher.config import settings_path

    if args.config_cmd == "gui":
        return _open_panel()
    if args.config_cmd == "path":
        print(settings_path())
        return 0
    if args.config_cmd == "get":
        value = settings.get(args.key)
        if value is None:
            print(
                f"Unknown setting '{args.key}'. Known: {', '.join(settings.known_keys())}",
                file=sys.stderr,
            )
            return 2
        print(value)
        return 0
    if args.config_cmd == "set":
        if args.key not in settings.known_keys():
            print(
                f"Unknown setting '{args.key}'. Known: {', '.join(settings.known_keys())}",
                file=sys.stderr,
            )
            return 2
        current = getattr(settings.Settings(), args.key)
        raw: object = args.value
        if isinstance(current, bool):
            raw = args.value.strip().lower() not in ("0", "false", "no", "off")
        elif isinstance(current, int):
            try:
                raw = int(args.value)
            except ValueError:
                print(f"'{args.key}' expects an integer.", file=sys.stderr)
                return 2
        settings.save({args.key: raw})
        print(f"{args.key} = {raw}")
        return 0
    conf = settings.load().to_dict()
    for key in sorted(conf):
        print(f"{key:<20} {conf[key]}")
    return 0


def _open_panel(section: str = "Settings") -> int:
    """Open the configuration panel, or explain why it cannot be opened.

    Some Linux distributions ship Python without tkinter; the failure should
    name the package to install rather than surface an ImportError traceback.
    """
    try:
        from launcher.panel import open_panel
    except ImportError as exc:
        print(f"The settings panel needs tkinter ({exc}).", file=sys.stderr)
        print("Debian/Ubuntu: sudo apt install python3-tk", file=sys.stderr)
        print("Meanwhile `palaunch config set <key> <value>` does the same job.", file=sys.stderr)
        return 1
    return open_panel(section)


def _cmd_settings(args: argparse.Namespace) -> int:
    return _open_panel(args.section)


def _cmd_secret(args: argparse.Namespace) -> int:
    from launcher import secrets

    try:
        if args.secret_cmd == "set":
            value = args.value
            if value is None:
                import getpass

                value = getpass.getpass(f"Value for '{args.name}': ")
            secrets.set_secret(args.name, value)
            print(f"Stored '{args.name}' — use it as {{{{ secret.{args.name} }}}}")
            return 0
        if args.secret_cmd == "rm":
            secrets.delete(args.name)
            print(f"Removed '{args.name}'")
            return 0
        names = secrets.list_names()
        if not names:
            print("No secrets stored yet.")
            return 0
        for name in names:
            print(name)
        return 0
    except secrets.SecretError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _cmd_sync(args: argparse.Namespace) -> int:
    from launcher import settings, sync

    try:
        if args.init is not None:
            remote = args.init or settings.load().sync_remote
            directory = sync.init(remote)
            print(f"Initialised git repo in {directory}" + (f" -> {remote}" if remote else ""))
            if remote:
                settings.save({"sync_remote": remote})
        if args.status:
            print(sync.status())
            return 0
        print(sync.sync(message=args.message, push=not args.no_push))
        return 0
    except sync.SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _cmd_schema(args: argparse.Namespace) -> int:
    import json

    from launcher import schema

    if args.stdout:
        print(json.dumps(schema.build(), indent=2))
        return 0
    path = schema.write(Path(args.output) if args.output else None)
    print(f"Wrote {path}")
    print(f"Add to a profile file:\n  {schema.modeline(path)}")
    return 0


# ── parser ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="palaunch", description="Profile Auto Launcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_p = sub.add_parser("list", help="List available profiles")
    list_p.add_argument("--triggers", action="store_true", help="Also show trigger schedules")
    list_p.set_defaults(func=_cmd_list)

    run_p = sub.add_parser("run", help="Run a profile by name (or the default)")
    run_p.add_argument("name", nargs="?", help="Profile name; uses default if omitted")
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what each step would do without executing anything",
    )
    run_p.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="STEP",
        help="Run only steps with this name/id/type (repeatable)",
    )
    run_p.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="STEP",
        help="Skip steps with this name/id/type (repeatable)",
    )
    run_p.set_defaults(func=_cmd_run)

    last_p = sub.add_parser("last", help="Re-run the most recently run profile")
    last_p.add_argument("--dry-run", action="store_true", help="Describe instead of executing")
    last_p.set_defaults(func=_cmd_last)

    stop_p = sub.add_parser("stop", help="Run teardown and close what a profile started")
    stop_p.add_argument("name", nargs="?", help="Profile name; uses default if omitted")
    stop_p.add_argument("--all", action="store_true", help="Stop every running profile")
    stop_p.set_defaults(func=_cmd_stop)

    switch_p = sub.add_parser("switch", help="Stop everything running, then run this profile")
    switch_p.add_argument("name", help="Profile to switch to")
    switch_p.set_defaults(func=_cmd_switch)

    sub.add_parser("status", help="Show running profiles and their processes").set_defaults(
        func=_cmd_status
    )

    validate_p = sub.add_parser("validate", help="Check every profile YAML for schema errors")
    validate_p.add_argument("--schema", action="store_true", help="Also refresh the JSON Schema")
    validate_p.set_defaults(func=_cmd_validate)

    new_p = sub.add_parser("new", help="Scaffold a new profile YAML")
    new_p.add_argument("name", help="Profile display name")
    new_p.add_argument("--edit", action="store_true", help="Open it in your editor")
    new_p.add_argument("--gui", action="store_true", help="Open it in the graphical editor")
    new_p.set_defaults(func=_cmd_new)

    edit_p = sub.add_parser("edit", help="Open a profile (or the profiles dir) in your editor")
    edit_p.add_argument("name", nargs="?", help="Profile name; opens the folder if omitted")
    edit_p.add_argument("--gui", action="store_true", help="Use the graphical step editor")
    edit_p.set_defaults(func=_cmd_edit)

    sub.add_parser("pick", help="Open the HUD picker").set_defaults(func=_cmd_pick)

    tray_p = sub.add_parser("tray", help="Run in the system tray with hotkeys and triggers")
    tray_p.add_argument("--no-scheduler", action="store_true", help="Ignore profile triggers")
    tray_p.set_defaults(func=_cmd_tray)

    sub.add_parser("where", help="Show config paths and hotkeys").set_defaults(func=_cmd_where)

    logs_p = sub.add_parser("logs", help="Show the launcher log")
    logs_p.add_argument("-n", "--lines", type=int, default=50, help="How many lines to show")
    logs_p.add_argument("-f", "--follow", action="store_true", help="Keep printing new lines")
    logs_p.add_argument("--path", action="store_true", help="Print the log file path and exit")
    logs_p.set_defaults(func=_cmd_logs)

    history_p = sub.add_parser("history", help="Show past runs")
    history_p.add_argument("--profile", help="Only this profile")
    history_p.add_argument("--limit", type=int, default=20, help="How many entries")
    history_p.add_argument("-v", "--verbose", action="store_true", help="Include per-step results")
    history_p.add_argument("--stats", action="store_true", help="Per-step timing and failure rates")
    history_p.add_argument("--clear", action="store_true", help="Delete the history file")
    history_p.set_defaults(func=_cmd_history)

    record_p = sub.add_parser("record", help="Watch what you open and write it out as a profile")
    record_p.add_argument("name", help="Name for the recorded profile")
    record_p.add_argument("--duration", type=float, default=120.0, help="Seconds to record")
    record_p.add_argument("--output", help="Write here instead of the profiles dir")
    record_p.add_argument("--force", action="store_true", help="Overwrite an existing file")
    record_p.set_defaults(func=_cmd_record)

    config_p = sub.add_parser("config", help="Read or change global settings")
    config_sub = config_p.add_subparsers(dest="config_cmd")
    config_sub.add_parser("list", help="Show every setting")
    get_p = config_sub.add_parser("get", help="Print one setting")
    get_p.add_argument("key")
    set_p = config_sub.add_parser("set", help="Change one setting")
    set_p.add_argument("key")
    set_p.add_argument("value")
    config_sub.add_parser("path", help="Print the settings file path")
    config_sub.add_parser("gui", help="Open the graphical configuration panel")
    config_p.set_defaults(func=_cmd_config, config_cmd="list")

    settings_p = sub.add_parser("settings", help="Open the graphical configuration panel")
    settings_p.add_argument(
        "section",
        nargs="?",
        default="Settings",
        choices=list(PANEL_SECTIONS),
        help="Which page to open (default: Settings)",
    )
    settings_p.set_defaults(func=_cmd_settings)

    secret_p = sub.add_parser("secret", help="Manage secrets in the OS credential store")
    secret_sub = secret_p.add_subparsers(dest="secret_cmd")
    secret_set = secret_sub.add_parser("set", help="Store a secret")
    secret_set.add_argument("name")
    secret_set.add_argument("value", nargs="?", help="Omit to be prompted without echo")
    secret_rm = secret_sub.add_parser("rm", help="Delete a secret")
    secret_rm.add_argument("name")
    secret_sub.add_parser("list", help="List known secret names")
    secret_p.set_defaults(func=_cmd_secret, secret_cmd="list")

    sync_p = sub.add_parser("sync", help="Sync the profiles directory through git")
    sync_p.add_argument(
        "--init",
        nargs="?",
        const="",
        metavar="REMOTE",
        help="Create the repo (optionally setting the remote URL)",
    )
    sync_p.add_argument("--status", action="store_true", help="Show repo status and exit")
    sync_p.add_argument("-m", "--message", default="", help="Commit message")
    sync_p.add_argument("--no-push", action="store_true", help="Commit and rebase, don't push")
    sync_p.set_defaults(func=_cmd_sync)

    schema_p = sub.add_parser("schema", help="Write the JSON Schema for profile files")
    schema_p.add_argument("--output", help="Write here instead of the config dir")
    schema_p.add_argument("--stdout", action="store_true", help="Print instead of writing")
    schema_p.set_defaults(func=_cmd_schema)

    return parser


def _force_utf8_stdio() -> None:
    """Avoid UnicodeEncodeError on legacy Windows codepages (cp1250, cp1252).

    The marker glyphs we print (★ ▶ ✓) are non-ASCII; without this, palaunch
    crashes on a default `cmd.exe` console. `reconfigure` is a no-op on streams
    that already speak UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # `palaunch history | head` closes the pipe while we are still
        # printing. Point the rest of stdout at devnull so the interpreter's
        # shutdown flush cannot raise a second time and print a traceback.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())

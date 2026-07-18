"""Command-line entry point."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from launcher.config import (
    PLATFORM,
    Profile,
    config_dir,
    default_profile,
    discover_profiles,
    find_profile,
    load_profile,
    profile_search_dirs,
    profiles_dir,
)
from launcher.executor import dry_run_profile, format_result, run_profile
from launcher.notify import notify

_NEW_PROFILE_TEMPLATE = """\
name: {name}
description: Describe what this profile sets up
icon: "\\U0001F680"

steps:
  - type: env
    set:
      PAL_SESSION: {slug}

  - type: url
    name: example tab
    url: https://example.com

  # - type: app
  #   name: VS Code
  #   path:
  #     windows: "%LOCALAPPDATA%\\\\Programs\\\\Microsoft VS Code\\\\Code.exe"
  #     linux:   /usr/bin/code

  # - type: command
  #   name: pull latest
  #   detach: false
  #   timeout: 30        # seconds; kill if it runs longer
  #   retries: 1         # retry once on failure
  #   optional: true     # failure doesn't fail the profile
  #   run: ["git", "pull", "--ff-only"]
"""


def _execute(profile: Profile, dry_run: bool = False) -> int:
    if dry_run:
        print(f"▶ dry run of profile: {profile.name}")
        for res in dry_run_profile(profile):
            print(format_result(res))
        return 0
    print(f"▶ running profile: {profile.name}")
    results = run_profile(profile, on_result=lambda r: print(format_result(r)))
    failed = sum(1 for r in results if r.counts_as_failure)
    ok = len(results) - failed
    if failed == 0:
        notify(f"{profile.name} — profile finished", f"✓ all {ok} steps succeeded")
    else:
        notify(f"{profile.name} — profile finished with errors", f"{ok} ok, {failed} failed")
    return 0 if failed == 0 else 1


def _resolve(name: str | None) -> Profile | None:
    return find_profile(name) if name else default_profile(discover_profiles())


def _cmd_list(_args: argparse.Namespace) -> int:
    profiles = discover_profiles()
    if not profiles:
        print(f"No profiles found. Drop YAML files in {profiles_dir()}.")
        return 1
    for p in profiles:
        marker = "★" if p.default else " "
        icon = f"{p.icon} " if p.icon else ""
        steps = f"({len(p.steps)} steps)"
        print(f" {marker} {icon}{p.name:<16} {steps:<12} {p.description}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    profile = _resolve(args.name)
    if profile is None:
        target = args.name or "default"
        print(f"Profile '{target}' not found.", file=sys.stderr)
        return 2
    return _execute(profile, dry_run=args.dry_run)


def _cmd_validate(_args: argparse.Namespace) -> int:
    paths = [
        path
        for directory in profile_search_dirs()
        if directory.is_dir()
        for path in sorted(directory.glob("*.y*ml"))
    ]
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
        print(f" ✓ {path.name}: '{prof.name}' — {len(prof.steps)} steps")
    print(f"{len(paths) - errors}/{len(paths)} profiles valid.")
    return 0 if errors == 0 else 1


def _cmd_new(args: argparse.Namespace) -> int:
    directory = profiles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    slug = args.name.lower().replace(" ", "-")
    path = directory / f"{slug}.yaml"
    if path.exists():
        print(f"Profile file already exists: {path}", file=sys.stderr)
        return 1
    path.write_text(
        _NEW_PROFILE_TEMPLATE.format(name=args.name, slug=slug), encoding="utf-8"
    )
    print(f"Created {path}")
    if args.edit:
        return _open_in_editor(path)
    print(f"Edit it, then try: palaunch run {args.name} --dry-run")
    return 0


def _open_in_editor(path: Path) -> int:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    try:
        if editor:
            subprocess.run([editor, str(path)], check=False)
        elif PLATFORM == "windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif PLATFORM == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError as exc:
        print(f"Could not open editor: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    if not args.name:
        return _open_in_editor(profiles_dir())
    profile = find_profile(args.name)
    if profile is None or profile.source_path is None:
        print(f"Profile '{args.name}' not found.", file=sys.stderr)
        return 2
    return _open_in_editor(profile.source_path)


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


def _cmd_pick(_args: argparse.Namespace) -> int:
    from launcher.hud import pick_and_run

    profiles = discover_profiles()
    if not profiles:
        print("No profiles to pick from.", file=sys.stderr)
        return 1
    return pick_and_run(profiles)


def _cmd_tray(_args: argparse.Namespace) -> int:
    from launcher.hotkey import run_hotkey
    from launcher.hud import pick_and_run
    from launcher.tray import run_tray

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

    def open_hud() -> None:
        pick_and_run(discover_profiles())

    auto = next((p for p in profiles if p.autostart), None)
    if auto is not None:
        print(f"▶ autostart profile: {auto.name}")
        import threading

        threading.Thread(target=execute, args=(auto,), daemon=True).start()

    run_hotkey(open_hud)
    run_tray(profiles, execute, open_hud)
    return 0


def _cmd_where(_args: argparse.Namespace) -> int:
    print(f"config dir:   {config_dir()}")
    print(f"profiles dir: {profiles_dir()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="palaunch", description="Profile Auto Launcher")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List available profiles").set_defaults(func=_cmd_list)

    run_p = sub.add_parser("run", help="Run a profile by name (or the default)")
    run_p.add_argument("name", nargs="?", help="Profile name; uses default if omitted")
    run_p.add_argument(
        "--dry-run", action="store_true",
        help="Print what each step would do without executing anything",
    )
    run_p.set_defaults(func=_cmd_run)

    last_p = sub.add_parser("last", help="Re-run the most recently run profile")
    last_p.add_argument("--dry-run", action="store_true", help="Describe instead of executing")
    last_p.set_defaults(func=_cmd_last)

    sub.add_parser(
        "validate", help="Check every profile YAML for schema errors"
    ).set_defaults(func=_cmd_validate)

    new_p = sub.add_parser("new", help="Scaffold a new profile YAML")
    new_p.add_argument("name", help="Profile display name")
    new_p.add_argument("--edit", action="store_true", help="Open it in your editor")
    new_p.set_defaults(func=_cmd_new)

    edit_p = sub.add_parser("edit", help="Open a profile (or the profiles dir) in your editor")
    edit_p.add_argument("name", nargs="?", help="Profile name; opens the folder if omitted")
    edit_p.set_defaults(func=_cmd_edit)

    sub.add_parser("pick", help="Open the HUD picker").set_defaults(func=_cmd_pick)
    sub.add_parser("tray", help="Run in the system tray with global hotkey").set_defaults(func=_cmd_tray)
    sub.add_parser("where", help="Show config paths").set_defaults(func=_cmd_where)
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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

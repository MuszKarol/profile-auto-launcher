"""Command-line entry point."""
from __future__ import annotations

import argparse
import sys
import threading

from launcher.config import (
    config_dir,
    default_profile,
    discover_profiles,
    find_profile,
    profiles_dir,
)
from launcher.executor import format_result, run_profile


def _cmd_list(_args: argparse.Namespace) -> int:
    profiles = discover_profiles()
    if not profiles:
        print(f"No profiles found. Drop YAML files in {profiles_dir()} or ./profiles.")
        return 1
    for p in profiles:
        marker = "★" if p.default else " "
        print(f" {marker} {p.name:<16} {p.description}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    profile = find_profile(args.name) if args.name else default_profile(discover_profiles())
    if profile is None:
        target = args.name or "default"
        print(f"Profile '{target}' not found.", file=sys.stderr)
        return 2
    print(f"▶ running profile: {profile.name}")
    results = run_profile(profile, on_result=lambda r: print(format_result(r)))
    failed = [r for r in results if not r.ok]
    return 0 if not failed else 1


def _cmd_pick(_args: argparse.Namespace) -> int:
    from launcher.hud import pick_profile

    profiles = discover_profiles()
    if not profiles:
        print("No profiles to pick from.", file=sys.stderr)
        return 1
    chosen = pick_profile(profiles)
    if chosen is None:
        return 0
    return _cmd_run(argparse.Namespace(name=chosen.name))


def _cmd_tray(_args: argparse.Namespace) -> int:
    from launcher.hotkey import run_hotkey
    from launcher.hud import pick_profile
    from launcher.tray import run_tray

    profiles = discover_profiles()
    if not profiles:
        print("No profiles found — tray has nothing to launch.", file=sys.stderr)
        return 1

    def execute(p) -> None:
        results = run_profile(p, on_result=lambda r: print(format_result(r)))
        failed = sum(1 for r in results if not r.ok)
        print(f"✓ {p.name}: {len(results) - failed} ok, {failed} failed")

    def open_hud() -> None:
        chosen = pick_profile(discover_profiles())
        if chosen:
            execute(chosen)

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
    run_p.set_defaults(func=_cmd_run)

    sub.add_parser("pick", help="Open the HUD picker").set_defaults(func=_cmd_pick)
    sub.add_parser("tray", help="Run in the system tray with global hotkey").set_defaults(func=_cmd_tray)
    sub.add_parser("where", help="Show config paths").set_defaults(func=_cmd_where)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

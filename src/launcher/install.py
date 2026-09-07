"""What the setup wizard actually does.

Installing the launcher means five small things, and every one of them is
platform-specific: put the program somewhere on `PATH`, copy the sample
profiles, register a start-at-login entry, add a menu shortcut, and write the
JSON Schema. They live here, without a widget in sight, so the wizard, the
CLI and the tests all drive the same code — and so an uninstall can undo
exactly what an install did.

Nothing here touches the user's profiles on the way out: uninstalling removes
what setup created, never what the user wrote.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from launcher import branding
from launcher.config import PLATFORM, config_dir, profiles_dir
from launcher.logging_setup import get_logger

log = get_logger("install")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DESKTOP_FILE = "profile-auto-launcher.desktop"
LAUNCH_AGENT = "com.profile-auto-launcher.tray.plist"
STARTUP_LINK = "Profile Auto Launcher.lnk"
STARTUP_FALLBACK = "Profile Auto Launcher.cmd"


@dataclass
class Options:
    """What the wizard was asked to do."""

    target: Path | None = None  # where to copy a frozen binary
    autostart: bool = True
    shortcut: bool = True
    sample_profiles: bool = True
    schema: bool = True


def is_frozen() -> bool:
    """True inside a PyInstaller build, where there is a binary to copy."""
    return bool(getattr(sys, "frozen", False))


def program() -> Path:
    """The palaunch executable to register — the frozen binary, or the script
    the current interpreter installed."""
    if is_frozen():
        return Path(sys.executable)
    found = shutil.which("palaunch")
    if found:
        return Path(found)
    # Running from a checkout with no console script: the module still works.
    return Path(sys.executable)


def windowed_program(exe: Path) -> Path:
    """`palaunchw` next to `palaunch`, which starts without a console window."""
    for candidate in (
        exe.with_name("palaunchw" + exe.suffix),
        exe.with_name("palaunchw"),
    ):
        if candidate.exists():
            return candidate
    return exe


def tray_command(exe: Path) -> list[str]:
    """The argv an autostart entry should run."""
    windowed = windowed_program(exe)
    if windowed.name.startswith("palaunch"):
        return [str(windowed), "tray"]
    return [str(windowed), "-m", "launcher", "tray"]


def default_target() -> Path:
    if PLATFORM == "windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "Programs" / "ProfileAutoLauncher"
    return Path.home() / ".local" / "bin"


def autostart_dir() -> Path:
    if PLATFORM == "windows":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    if PLATFORM == "darwin":
        return Path.home() / "Library" / "LaunchAgents"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"


def menu_dir() -> Path:
    if PLATFORM == "windows":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if PLATFORM == "darwin":
        return Path.home() / "Applications"
    return (
        Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
    )


def icon_path() -> Path:
    """Where the app icon is written, so shortcuts can point at a real file."""
    if PLATFORM == "windows":
        return config_dir() / "palaunch.ico"
    return config_dir() / "palaunch.png"


def bundled(name: str) -> Path | None:
    """A directory shipped alongside the program (`profiles/`, `plugins/`).

    Three shapes to cover: a PyInstaller bundle, a release archive where the
    directory sits next to the binary, and a source checkout two levels above
    this file.
    """
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / name if getattr(sys, "_MEIPASS", "") else None,
        Path(sys.executable).parent / name if is_frozen() else None,
        Path(__file__).resolve().parents[2] / name,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            return candidate
    return None


# ── steps ────────────────────────────────────────────────────────────────


def plan(options: Options) -> list[str]:
    """The install, described before it happens. The wizard shows this."""
    exe = program()
    lines = []
    if is_frozen() and options.target:
        lines.append(f"Copy palaunch to {options.target}")
    else:
        lines.append(f"Use the installed palaunch at {exe}")
    if options.sample_profiles:
        lines.append(f"Copy the sample profiles into {profiles_dir()} (existing files are kept)")
    if options.schema:
        lines.append("Write the profile JSON Schema so editors can validate profiles")
    if options.shortcut:
        lines.append(f"Add a menu entry in {menu_dir()}")
    if options.autostart:
        lines.append(f"Start the tray at login from {autostart_dir()}")
    return lines


def _write_icon() -> Path:
    target = icon_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = branding.ico_bytes() if PLATFORM == "windows" else branding.png_bytes(256)
    target.write_bytes(data)
    return target


def _copy_tree(source: Path, destination: Path, log_line) -> int:
    copied = 0
    destination.mkdir(parents=True, exist_ok=True)
    for item in sorted(source.iterdir()):
        if item.is_dir():
            continue
        target = destination / item.name
        if target.exists():
            continue
        shutil.copy2(item, target)
        log_line(f"  + {item.name}")
        copied += 1
    return copied


def _install_binary(options: Options, log_line) -> Path:
    exe = program()
    if not (is_frozen() and options.target):
        return exe
    options.target.mkdir(parents=True, exist_ok=True)
    installed = exe
    for source in {exe, windowed_program(exe)}:
        target = options.target / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
            target.chmod(0o755)
            log_line(f"installed {target}")
        if source == exe:
            installed = target
    return installed


def _desktop_entry(exe: Path, icon: Path, autostart: bool) -> str:
    command = " ".join(tray_command(exe)) if autostart else f'"{exe}" pick'
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=Profile Auto Launcher\n"
        "Comment=Open a whole working context at once\n"
        f"Exec={command}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _launch_agent(exe: Path) -> str:
    arguments = "".join(f"        <string>{part}</string>\n" for part in tray_command(exe))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        "    <string>com.profile-auto-launcher.tray</string>\n"
        "    <key>ProgramArguments</key>\n"
        f"    <array>\n{arguments}    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def _windows_shortcut(link: Path, exe: Path, arguments: str, icon: Path) -> bool:
    """Create a .lnk through PowerShell. False when PowerShell refuses."""
    script = (
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
        f"$s.TargetPath = '{exe}';"
        f"$s.Arguments = '{arguments}';"
        f"$s.WorkingDirectory = '{exe.parent}';"
        f"$s.IconLocation = '{icon}';"
        "$s.Save()"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and link.exists()


def _register_autostart(exe: Path, icon: Path, log_line) -> None:
    directory = autostart_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if PLATFORM == "windows":
        link = directory / STARTUP_LINK
        windowed = windowed_program(exe)
        if _windows_shortcut(link, windowed, "tray", icon):
            log_line(f"autostart shortcut {link}")
            return
        fallback = directory / STARTUP_FALLBACK
        fallback.write_text(f'@echo off\r\nstart "" "{windowed}" tray\r\n', encoding="utf-8")
        log_line(f"autostart command {fallback}")
        return
    if PLATFORM == "darwin":
        plist = directory / LAUNCH_AGENT
        plist.write_text(_launch_agent(exe), encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, check=False)
        subprocess.run(["launchctl", "load", str(plist)], capture_output=True, check=False)
        log_line(f"launch agent {plist}")
        return
    entry = directory / DESKTOP_FILE
    entry.write_text(_desktop_entry(exe, icon, autostart=True), encoding="utf-8")
    log_line(f"autostart entry {entry}")


def _register_shortcut(exe: Path, icon: Path, log_line) -> None:
    directory = menu_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if PLATFORM == "windows":
        link = directory / STARTUP_LINK
        if _windows_shortcut(link, windowed_program(exe), "pick", icon):
            log_line(f"start menu entry {link}")
        return
    if PLATFORM == "darwin":
        # macOS wants an .app bundle; a frozen binary is not one, so the
        # honest thing is to say the menu entry was skipped.
        log_line("menu entry skipped on macOS — run `palaunch tray` or use the login item")
        return
    entry = directory / DESKTOP_FILE
    entry.write_text(_desktop_entry(exe, icon, autostart=False), encoding="utf-8")
    log_line(f"menu entry {entry}")


def install(options: Options | None = None, log_line=print) -> list[str]:
    """Run the install. Returns the notes worth showing when it finishes."""
    options = options or Options()
    notes: list[str] = []
    exe = _install_binary(options, log_line)
    icon = _write_icon()
    log_line(f"icon {icon}")

    if options.sample_profiles:
        source = bundled("profiles")
        if source is None:
            log_line("no sample profiles bundled — skipping")
        else:
            count = _copy_tree(source, profiles_dir(), log_line)
            log_line(f"sample profiles: {count} copied into {profiles_dir()}")
        plugins = bundled("plugins")
        if plugins is not None:
            _copy_tree(plugins, config_dir() / "plugins", log_line)

    if options.schema:
        from launcher import schema

        log_line(f"schema {schema.write()}")

    if options.shortcut:
        _register_shortcut(exe, icon, log_line)
    if options.autostart:
        _register_autostart(exe, icon, log_line)
        notes.append("The tray starts automatically at your next login.")

    directory = str(exe.parent)
    if not _on_path(directory):
        notes.append(f"Add {directory} to your PATH to run `palaunch` from a terminal.")
    notes.append(f"Profiles live in {profiles_dir()}.")
    log.info("install finished (%s)", exe)
    return notes


def _on_path(directory: str) -> bool:
    entries = {
        os.path.normcase(os.path.normpath(part))
        for part in (os.environ.get("PATH") or "").split(os.pathsep)
        if part
    }
    return os.path.normcase(os.path.normpath(directory)) in entries


def installed_entries() -> list[Path]:
    """Everything an install created that an uninstall should remove."""
    candidates = [
        autostart_dir() / DESKTOP_FILE,
        autostart_dir() / LAUNCH_AGENT,
        autostart_dir() / STARTUP_LINK,
        autostart_dir() / STARTUP_FALLBACK,
        menu_dir() / DESKTOP_FILE,
        menu_dir() / STARTUP_LINK,
        icon_path(),
    ]
    return [path for path in candidates if path.exists()]


def is_registered() -> bool:
    """True when a start-at-login entry exists for this user."""
    return any(
        (autostart_dir() / name).exists()
        for name in (DESKTOP_FILE, LAUNCH_AGENT, STARTUP_LINK, STARTUP_FALLBACK)
    )


def uninstall(log_line=print, remove_program: bool = False) -> list[str]:
    """Undo what `install` created. Profiles and settings are left alone."""
    removed = []
    for path in installed_entries():
        if PLATFORM == "darwin" and path.name == LAUNCH_AGENT:
            subprocess.run(["launchctl", "unload", str(path)], capture_output=True, check=False)
        try:
            path.unlink()
        except OSError as exc:
            log_line(f"could not remove {path}: {exc}")
            continue
        log_line(f"removed {path}")
        removed.append(str(path))
    if remove_program and is_frozen():
        directory = Path(sys.executable).parent
        log_line(f"the program itself is still in {directory} — delete it when it is not running")
    log_line(f"profiles kept in {profiles_dir()}")
    return removed

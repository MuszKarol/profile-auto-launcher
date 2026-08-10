# PyInstaller spec — builds a self-contained palaunch binary.
#
#   pip install pyinstaller ".[full]"
#   pyinstaller packaging/palaunch.spec
#
# Produces dist/palaunch (console) and dist/palaunchw (no console window on
# Windows). The two share one Analysis so the heavy import scan runs once.
#
# The optional integrations are listed as hidden imports because they are only
# ever imported inside functions — PyInstaller's static scan would miss them
# and the frozen build would silently lose the tray, hotkeys and secrets.

from PyInstaller.utils.hooks import collect_submodules

hidden = [
    "launcher.editor",
    "launcher.hud",
    "launcher.record",
    "launcher.scheduler",
    "launcher.secrets",
    "launcher.sync",
    "launcher.tray",
    "launcher.windows",
]
for optional in ("pystray", "PIL", "pynput", "keyring", "psutil"):
    try:
        hidden += collect_submodules(optional)
    except Exception:
        pass  # building without an extra installed is fine

analysis = Analysis(
    ["../src/launcher/__main__.py"],
    pathex=["../src"],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

console_exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="palaunch",
    console=True,
    upx=True,
    strip=False,
)

windowed_exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="palaunchw",
    console=False,
    upx=True,
    strip=False,
)

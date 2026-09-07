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

import os

from PyInstaller.utils.hooks import collect_submodules

# PyInstaller resolves relative paths against the working directory, not the
# spec file, so `pyinstaller packaging/palaunch.spec` from the repo root would
# otherwise look for sources one level above the repo. SPECPATH is injected by
# PyInstaller and always points at this file's directory.
ROOT = os.path.join(SPECPATH, "..")  # noqa: F821 - injected by PyInstaller
SRC = os.path.join(ROOT, "src")
ICON = os.path.join(ROOT, "assets", "icon.ico")

# Bundled next to the binary so the setup wizard can copy them on first run.
datas = [
    (os.path.join(ROOT, "profiles"), "profiles"),
    (os.path.join(ROOT, "plugins"), "plugins"),
    (os.path.join(ROOT, "assets"), "assets"),
]

hidden = [
    "launcher.apps",
    "launcher.branding",
    "launcher.editor",
    "launcher.fuzzy",
    "launcher.hud",
    "launcher.install",
    "launcher.installer",
    "launcher.mirror",
    "launcher.panel",
    "launcher.panel_model",
    "launcher.record",
    "launcher.scaffold",
    "launcher.scheduler",
    "launcher.secrets",
    "launcher.sync",
    "launcher.tray",
    "launcher.ui",
    "launcher.windows",
]
for optional in ("pystray", "PIL", "pynput", "keyring", "psutil"):
    try:
        hidden += collect_submodules(optional)
    except Exception:
        pass  # building without an extra installed is fine

analysis = Analysis(
    [os.path.join(SRC, "launcher", "__main__.py")],
    pathex=[SRC],
    binaries=[],
    datas=datas,
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
    icon=ICON,
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
    icon=ICON,
)

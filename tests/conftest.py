"""Shared fixtures.

Every test runs against a throwaway config directory. `PAL_CONFIG_DIR` is the
one knob that redirects profiles, settings, state, history and logs at once,
so nothing in the suite can touch a developer's real profiles.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch) -> Path:
    from launcher import logging_setup, settings

    config = tmp_path / "config"
    (config / "profiles").mkdir(parents=True)
    monkeypatch.setenv("PAL_CONFIG_DIR", str(config))
    monkeypatch.delenv("PAL_INCLUDE_CWD", raising=False)
    monkeypatch.delenv("PAL_HOTKEY", raising=False)
    monkeypatch.delenv("PAL_NOTIFY", raising=False)
    # Both modules memoise; a leaked cache would leak between tests.
    settings._cache = None
    logging_setup._configured = False
    yield config
    settings._cache = None


@pytest.fixture
def profiles_dir(isolated_config) -> Path:
    return isolated_config / "profiles"


@pytest.fixture
def write_profile(profiles_dir):
    def _write(name: str, body: str) -> Path:
        path = profiles_dir / f"{name}.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def no_notifications(monkeypatch):
    """Keep tests from firing real desktop toasts."""
    monkeypatch.setattr("launcher.notify.notify", lambda *_a, **_k: None)


class _Gui:
    """Builds Tk windows, turning "Tk does not work here" into a skip.

    A throwaway probe root would be the obvious guard and is the wrong one:
    a failed `Tk()` leaves the interpreter part-initialised, so the *next*
    `Tk()` gets further before failing — the probe passes and the real window
    still explodes. Windows CI runners ship a Python whose Tcl/Tk data files
    are missing and do exactly that. So there is one attempt per window, and
    it is the real one.

    Windows are built as children of one session-wide root. Tk dislikes being
    asked for a second root, and macOS dislikes it enough to take the whole
    interpreter down with a bus error partway through the suite — which is
    exactly what a test file full of windows used to do.
    """

    def __init__(self, tk, root) -> None:
        self.tk = tk
        self.root = root
        self.TclError = tk.TclError

    def build(self, factory, *args, **kwargs):
        if "parent" not in kwargs and self._accepts_parent(factory):
            kwargs["parent"] = self.root
        try:
            return factory(*args, **kwargs)
        except self.tk.TclError as exc:  # no display, or a broken Tcl/Tk
            pytest.skip(f"Tk cannot open a window here: {exc}")

    @staticmethod
    def _accepts_parent(factory) -> bool:
        import inspect

        try:
            return "parent" in inspect.signature(factory).parameters
        except (TypeError, ValueError):
            return False


@pytest.fixture(scope="session")
def tk_root():
    """One Tk root for the whole session; every window hangs off it."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk cannot open a window here: {exc}")
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def gui(tk_root):
    """Window builder, or a skip when tkinter is not installed at all.

    CI runs the Linux jobs under Xvfb so these tests actually execute there;
    everywhere else they skip instead of erroring.
    """
    return _Gui(pytest.importorskip("tkinter"), tk_root)


@pytest.fixture
def panel(gui):
    """A built configuration panel, destroyed when the test ends."""
    from launcher.panel import _Panel

    window = gui.build(_Panel)
    window.root.update()
    yield window
    try:
        window.root.destroy()
    except gui.TclError:
        pass

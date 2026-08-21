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


@pytest.fixture
def gui():
    """The tkinter module, or a skip when no display can be opened.

    CI runs the Linux jobs under Xvfb so these tests actually execute there;
    on a headless developer box they skip instead of erroring.
    """
    tk = pytest.importorskip("tkinter")
    try:
        probe = tk.Tk()
    except tk.TclError as exc:  # no DISPLAY, no window server
        pytest.skip(f"no display available: {exc}")
    probe.destroy()
    return tk


@pytest.fixture
def panel(gui):
    """A built configuration panel, destroyed when the test ends."""
    from launcher.panel import _Panel

    window = _Panel()
    window.root.update()
    yield window
    try:
        window.root.destroy()
    except gui.TclError:
        pass

from __future__ import annotations

import socket
import sys
from datetime import datetime

import pytest

from launcher import conditions, interp
from launcher.config import Profile

# ── conditions ───────────────────────────────────────────────────────────


def test_empty_condition_matches():
    assert conditions.matches({}, {}) is True


def test_platform_condition(monkeypatch):
    monkeypatch.setattr(conditions, "PLATFORM", "linux")
    assert conditions.matches({"platform": "linux"}, {})
    assert conditions.matches({"platform": ["windows", "linux"]}, {})
    assert not conditions.matches({"platform": "darwin"}, {})


def test_exists_and_not_exists(tmp_path):
    present = tmp_path / "here"
    present.write_text("x")
    missing = tmp_path / "gone"
    assert conditions.matches({"exists": str(present)}, {})
    assert not conditions.matches({"exists": str(missing)}, {})
    assert conditions.matches({"not_exists": str(missing)}, {})
    assert not conditions.matches({"not_exists": str(present)}, {})


def test_exists_requires_every_listed_path(tmp_path):
    present = tmp_path / "here"
    present.write_text("x")
    assert not conditions.matches({"exists": [str(present), str(tmp_path / "gone")]}, {})


def test_env_condition():
    assert conditions.matches({"env": {"MODE": "dev"}}, {"MODE": "dev"})
    assert not conditions.matches({"env": {"MODE": "dev"}}, {"MODE": "prod"})
    assert not conditions.matches({"env": {"MODE": "dev"}}, {})


def test_weekday_condition():
    monday = datetime(2026, 8, 10)  # a Monday
    assert conditions.matches({"weekday": ["mon"]}, {}, now=monday)
    assert conditions.matches({"weekday": ["monday", "friday"]}, {}, now=monday)
    assert not conditions.matches({"weekday": ["sat", "sun"]}, {}, now=monday)


@pytest.mark.parametrize(
    "window, hour, expected",
    [
        (["09:00", "17:00"], 12, True),
        (["09:00", "17:00"], 8, False),
        (["09:00", "17:00"], 18, False),
        (["22:00", "06:00"], 23, True),  # wraps past midnight
        (["22:00", "06:00"], 3, True),
        (["22:00", "06:00"], 12, False),
    ],
)
def test_time_between(window, hour, expected):
    now = datetime(2026, 8, 10, hour, 30)
    assert conditions.matches({"time_between": window}, {}, now=now) is expected


def test_time_between_rejects_a_malformed_window():
    assert not conditions.matches({"time_between": ["nine", "five"]}, {})
    assert not conditions.matches({"time_between": ["09:00"]}, {})


def test_hostname_condition():
    assert conditions.matches({"hostname": socket.gethostname()}, {})
    assert not conditions.matches({"hostname": "definitely-not-this-host"}, {})


def test_command_condition_uses_the_exit_code():
    assert conditions.matches({"command": [sys.executable, "-c", "pass"]}, {})
    assert not conditions.matches({"command": [sys.executable, "-c", "raise SystemExit(1)"]}, {})


def test_command_condition_survives_a_missing_binary():
    assert not conditions.matches({"command": ["definitely-not-a-real-binary-xyz"]}, {})


def test_process_conditions(monkeypatch):
    from launcher import sysprobe

    monkeypatch.setattr(sysprobe, "is_running", lambda name: name == "code")
    assert conditions.matches({"process_running": "code"}, {})
    assert not conditions.matches({"process_running": "slack"}, {})
    assert conditions.matches({"process_not_running": "slack"}, {})


def test_unknown_probe_result_fails_closed(monkeypatch):
    from launcher import sysprobe

    monkeypatch.setattr(sysprobe, "on_battery", lambda: None)
    monkeypatch.setattr(sysprobe, "wifi_ssid", lambda: None)
    assert not conditions.matches({"on_battery": True}, {})
    assert not conditions.matches({"on_battery": False}, {})
    assert not conditions.matches({"wifi_ssid": "Home"}, {})


def test_battery_and_wifi_when_known(monkeypatch):
    from launcher import sysprobe

    monkeypatch.setattr(sysprobe, "on_battery", lambda: True)
    monkeypatch.setattr(sysprobe, "wifi_ssid", lambda: "Home")
    assert conditions.matches({"on_battery": True}, {})
    assert not conditions.matches({"on_battery": False}, {})
    assert conditions.matches({"wifi_ssid": ["Home", "Office"]}, {})


def test_conditions_are_anded_together(monkeypatch):
    monkeypatch.setattr(conditions, "PLATFORM", "linux")
    assert not conditions.matches({"platform": "linux", "env": {"A": "1"}}, {"A": "2"})


# ── interpolation ────────────────────────────────────────────────────────


def _ctx(**kwargs) -> interp.Context:
    return interp.Context(**kwargs)


def test_plain_text_is_untouched():
    assert interp.render("nothing to see", _ctx()) == "nothing to see"


def test_profile_placeholders():
    profile = Profile(name="Dev", icon="D", description="desc", tags=["a", "b"])
    ctx = interp.Context.for_profile(profile, {})
    assert interp.render("{{ profile }}", ctx) == "Dev"
    assert interp.render("{{ profile.icon }}", ctx) == "D"
    assert interp.render("{{ profile.tags }}", ctx) == "a,b"


def test_env_and_vars_placeholders():
    ctx = _ctx(env={"HOME": "/home/x"}, vars={"repo": "main"})
    assert interp.render("{{ env.HOME }}/{{ vars.repo }}", ctx) == "/home/x/main"


def test_time_placeholders_accept_a_format():
    ctx = _ctx(now=datetime(2026, 8, 10, 14, 3, 12))
    assert interp.render("{{ date }}", ctx) == "2026-08-10"
    assert interp.render("{{ now:%H-%M }}", ctx) == "14-03"
    assert interp.render("{{ now }}", ctx) == "2026-08-10T14:03:12"


def test_unknown_placeholder_is_left_verbatim():
    # Dropping the token would silently change an argument list.
    assert interp.render("{{ nope }}", _ctx()) == "{{ nope }}"
    assert interp.render("{{ env.MISSING }}", _ctx()) == "{{ env.MISSING }}"


def test_whitespace_inside_braces_is_tolerated():
    ctx = _ctx(vars={"a": "1"})
    assert interp.render("{{vars.a}}-{{   vars.a   }}", ctx) == "1-1"


def test_render_any_walks_containers():
    ctx = _ctx(vars={"a": "X"})
    payload = {"k": ["{{ vars.a }}", {"n": "{{ vars.a }}"}], "plain": 3}
    assert interp.render_any(payload, ctx) == {"k": ["X", {"n": "X"}], "plain": 3}


def test_secret_failure_raises_interpolation_error(monkeypatch):
    from launcher import secrets

    monkeypatch.setattr(
        secrets,
        "get",
        lambda name: (_ for _ in ()).throw(secrets.SecretError("nope")),
    )
    with pytest.raises(interp.InterpolationError):
        interp.render("{{ secret.token }}", _ctx())


def test_resolved_secrets_are_collected_and_scrubbed(monkeypatch):
    from launcher import secrets

    monkeypatch.setattr(secrets, "get", lambda _n: "s3cret")
    ctx = _ctx()
    rendered = interp.render("Bearer {{ secret.token }}", ctx)
    assert rendered == "Bearer s3cret"
    assert interp.scrub(rendered, ctx) == "Bearer ***"


def test_has_secret_detects_references_in_containers():
    assert interp.has_secret("{{ secret.a }}")
    assert interp.has_secret(["x", {"k": "{{ secret.a }}"}])
    assert not interp.has_secret(["x", {"k": "{{ vars.a }}"}])


# ── subprocess environments ──────────────────────────────────────────────


def test_an_empty_env_means_inherit_the_parent():
    """A `when: {command: …}` evaluated with no env must still be able to run.

    Handing Windows an empty environment block makes CreateProcess fail before
    the program starts, which turned every such condition into False there.
    """
    from launcher.config import subprocess_env

    assert subprocess_env({}) is None
    assert subprocess_env(None) is None


def test_a_populated_env_is_passed_through_on_posix(monkeypatch):
    from launcher import config

    monkeypatch.setattr(config, "PLATFORM", "linux")
    assert config.subprocess_env({"A": "1"}) == {"A": "1"}


def test_windows_gets_the_variables_a_process_cannot_start_without(monkeypatch):
    from launcher import config

    monkeypatch.setattr(config, "PLATFORM", "windows")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("ComSpec", r"C:\Windows\cmd.exe")
    result = config.subprocess_env({"MY_VAR": "1"})
    assert result["MY_VAR"] == "1"
    assert result["SystemRoot"] == r"C:\Windows"


def test_windows_does_not_add_a_second_path_under_another_case(monkeypatch):
    from launcher import config

    monkeypatch.setattr(config, "PLATFORM", "windows")
    monkeypatch.setenv("PATH", "/parent")
    result = config.subprocess_env({"PATH": "/child"})
    assert [key for key in result if key.upper() == "PATH"] == ["PATH"]
    assert result["PATH"] == "/child"


def test_a_command_condition_runs_with_an_empty_env():
    assert conditions.matches({"command": [sys.executable, "-c", "pass"]}, {})

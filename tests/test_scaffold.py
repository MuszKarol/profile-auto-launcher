"""Turning a few answers into a profile file the loader accepts."""

from __future__ import annotations

import pytest

from launcher import scaffold


def test_no_preset_carries_an_emoji():
    """The interface is monochrome and text-only; presets seed copy, not glyphs."""
    for preset in scaffold.PRESETS:
        assert preset.title.isascii()
        assert preset.description.isascii()


def test_a_name_becomes_a_filename():
    assert scaffold.slugify("My Dev Setup") == "my-dev-setup"
    assert scaffold.slugify("  Work/Home  ") == "work-home"
    assert scaffold.slugify("!!!") == "profile"


def test_a_draft_becomes_a_profile_the_loader_accepts(monkeypatch, profiles_dir):
    monkeypatch.setattr(scaffold, "_resolve_app", lambda name: (f"/usr/bin/{name}", []))
    draft = scaffold.Draft(
        name="Dev",
        description="the desk",
        tags=["code"],
        apps=["code", "terminal", "notes"],
        urls=["https://example.com"],
        close=["slack"],
    )
    path = scaffold.write(draft)

    from launcher.config import load_profile

    profile = load_profile(path)
    assert profile.name == "Dev"
    assert [step.type for step in profile.steps] == ["kill", "app", "app", "app", "url"]
    # only the first two windows are tiled — a third has nowhere to go
    assert [step.window["position"] for step in profile.steps[1:3]] == ["left-half", "right-half"]
    assert profile.steps[3].window is None


def test_tiling_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(scaffold, "_resolve_app", lambda name: (f"/usr/bin/{name}", []))
    profile = scaffold.build(scaffold.Draft(name="Dev", apps=["code"], tile=False))
    assert "window" not in profile["steps"][0]


def test_an_app_the_index_does_not_know_is_kept_verbatim(monkeypatch):
    monkeypatch.setattr("launcher.apps.find", lambda name: None)
    profile = scaffold.build(scaffold.Draft(name="Dev", apps=["not-installed-yet"]))
    assert profile["steps"][0]["path"] == "not-installed-yet"


def test_a_draft_without_a_name_is_refused():
    with pytest.raises(ValueError, match="needs a name"):
        scaffold.build(scaffold.Draft(name="   "))


def test_writing_twice_refuses_unless_told_to_overwrite(profiles_dir):
    draft = scaffold.Draft(name="Dev")
    scaffold.write(draft)
    with pytest.raises(FileExistsError):
        scaffold.write(draft)
    assert scaffold.write(draft, overwrite=True).exists()


def test_the_optional_teardown_is_only_written_when_asked(profiles_dir):
    plain = scaffold.build(scaffold.Draft(name="A"))
    noisy = scaffold.build(scaffold.Draft(name="B", notify_on_stop=True))
    assert "teardown" not in plain
    assert noisy["teardown"][0]["type"] == "notify"


def test_every_preset_is_usable_as_a_starting_point():
    for preset in scaffold.PRESETS:
        draft = scaffold.Draft(
            name=preset.title,
            description=preset.description,
            tags=list(preset.tags),
            close=list(preset.close),
        )
        assert scaffold.build(draft)["name"] == preset.title
    assert scaffold.preset("nonsense").key == "blank"


def test_the_starter_template_loads(profiles_dir):
    from launcher.config import load_profile

    path = profiles_dir / "starter.yaml"
    path.write_text(scaffold.starter_yaml("Starter"), encoding="utf-8")
    assert load_profile(path).name == "Starter"


def test_the_cli_creates_a_profile_from_flags(profiles_dir, monkeypatch):
    from launcher.cli import main

    monkeypatch.setattr("launcher.apps.find", lambda name: None)
    assert main(["new", "Desk", "--app", "code", "--url", "https://example.com"]) == 0

    from launcher.config import find_profile

    profile = find_profile("Desk")
    assert [step.type for step in profile.steps] == ["app", "url"]

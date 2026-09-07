"""The application index, the matcher, and launching one app by name."""

from __future__ import annotations

import pytest

from launcher import apps, fuzzy

# ── matching ─────────────────────────────────────────────────────────────


def test_the_ranking_prefers_a_prefix_then_a_word_then_a_substring():
    assert fuzzy.score("fire", "Firefox") == fuzzy.PREFIX
    assert fuzzy.score("fox", "Fire Fox") == fuzzy.WORD_START
    assert fuzzy.score("efo", "Firefox") == fuzzy.SUBSTRING
    assert fuzzy.score("ffx", "Firefox") == fuzzy.SUBSEQUENCE
    assert fuzzy.score("zzz", "Firefox") is None


def test_dashes_and_underscores_start_words():
    assert fuzzy.score("code", "visual-studio_code") == fuzzy.WORD_START


def test_an_empty_query_matches_everything_and_an_empty_field_nothing():
    assert fuzzy.score("", "anything") == 0
    assert fuzzy.score("x", "") is None
    assert fuzzy.best("term", ["nope", "a terminal"]) == fuzzy.WORD_START
    assert fuzzy.best("term", ["nope", "nothing"]) is None


# ── .desktop parsing ─────────────────────────────────────────────────────

DESKTOP = """\
[Desktop Entry]
Type=Application
Name=Text Editor
Name[pl]=Edytor tekstu
Comment=Edit text files
Exec=gedit %U
Keywords=text;editor;
Terminal=false

[Desktop Action new-window]
Name=New Window
Exec=gedit --new-window
"""


def test_a_desktop_entry_is_read_without_its_localisations_or_actions():
    entry = apps.parse_desktop_entry(DESKTOP)
    assert entry["Name"] == "Text Editor"
    assert entry["Exec"] == "gedit %U"
    assert "Name[pl]" not in entry


def test_field_codes_are_stripped_from_the_command():
    assert apps._desktop_argv("gedit %U") == ["gedit"]
    assert apps._desktop_argv("flatpak run --file-forwarding org.x.App @@u %U @@") == [
        "flatpak",
        "run",
        "--file-forwarding",
        "org.x.App",
        "@@u",
        "@@",
    ]


def test_desktop_entries_are_indexed_from_the_xdg_directories(tmp_path, monkeypatch):
    share = tmp_path / "share" / "applications"
    share.mkdir(parents=True)
    (share / "editor.desktop").write_text(DESKTOP, encoding="utf-8")
    (share / "hidden.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Hidden\nExec=nope\nNoDisplay=true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(apps, "_xdg_dirs", lambda: [share])

    found = {app.name: app for app in apps._scan_desktop_entries()}
    assert set(found) == {"Text Editor"}
    assert found["Text Editor"].argv == ("gedit",)
    assert found["Text Editor"].source == "desktop"


# ── the index ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_index(monkeypatch):
    entries = [
        apps.App("Firefox", ("/usr/bin/firefox",), "desktop", comment="Web browser"),
        apps.App("firefox", ("/usr/bin/firefox",), "path", comment="/usr/bin"),
        apps.App("File Manager", ("/usr/bin/nautilus",), "desktop"),
        apps.App("fdisk", ("/sbin/fdisk",), "path"),
    ]
    monkeypatch.setattr(apps, "_scanners", lambda: [lambda: entries])
    apps.index(refresh=True)
    return entries


def test_the_index_keeps_one_entry_per_name(fake_index):
    names = [app.name for app in apps.index()]
    assert len(names) == len({name.lower() for name in names})


def test_search_ranks_a_named_application_above_a_bare_executable(fake_index):
    assert [app.name for app in apps.search("f", limit=2)] == ["Firefox", "File Manager"]


def test_search_needs_a_query_and_find_returns_the_best_match(fake_index):
    assert apps.search("") == []
    assert apps.find("fdis").name == "fdisk"
    assert apps.find("no such application anywhere") is None


def test_launching_by_name_spawns_and_tracks_it(fake_index, monkeypatch):
    from launcher import procs

    spawned = []

    class _Proc:
        pid = 4321

    monkeypatch.setattr(
        "launcher.executor.spawn_detached", lambda argv, **kw: spawned.append(argv) or _Proc()
    )
    detail = apps.launch("firefox")
    assert spawned == [["/usr/bin/firefox"]]
    assert "4321" in detail
    assert [proc.label for proc in procs.tracked(apps.LAUNCH_GROUP)] == ["Firefox"]


def test_launching_something_that_is_not_installed_says_so(fake_index):
    with pytest.raises(LookupError, match="no installed application"):
        apps.launch("definitely-not-installed")


def test_a_typed_command_line_resolves_through_the_index(fake_index, monkeypatch):
    monkeypatch.setattr(apps, "_which", lambda command: "")
    assert apps.resolve_command("firefox --private") == ["/usr/bin/firefox", "--private"]
    assert apps.resolve_command("") == []


def test_a_broken_scanner_does_not_empty_the_index(monkeypatch):
    def explode():
        raise RuntimeError("no")

    good = apps.App("Ok", ("/bin/ok",), "path")
    monkeypatch.setattr(apps, "_scanners", lambda: [explode, lambda: [good]])
    assert [app.name for app in apps.index(refresh=True)] == ["Ok"]


# ── the launch page's rows ───────────────────────────────────────────────


def test_launch_rows_put_profiles_before_apps(fake_index, write_profile):
    from launcher import panel_model

    write_profile("fire", "name: Fire Drill\nsteps: []\n")
    rows = panel_model.launch_rows("fir")
    assert rows[0] == {
        "kind": "profile",
        "name": "Fire Drill",
        "detail": "0 steps",
        "rank": fuzzy.PREFIX,
    }
    assert [row["kind"] for row in rows[1:]] == ["app", "app"]


def test_launch_rows_with_no_query_list_only_profiles(fake_index, write_profile):
    from launcher import panel_model

    write_profile("dev", "name: Dev\nsteps: []\n")
    assert [row["name"] for row in panel_model.launch_rows("")] == ["Dev"]

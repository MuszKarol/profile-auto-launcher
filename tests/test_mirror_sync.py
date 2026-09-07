"""The rsync mirror: the built-in walker, the rsync argv, and the step."""

from __future__ import annotations

import pytest

from launcher import mirror

# ── the built-in mirror ──────────────────────────────────────────────────


@pytest.fixture
def tree(tmp_path):
    source = tmp_path / "src"
    (source / "nested").mkdir(parents=True)
    (source / "a.txt").write_text("a", encoding="utf-8")
    (source / "nested" / "b.txt").write_text("b", encoding="utf-8")
    (source / "notes.log").write_text("noise", encoding="utf-8")
    return source, tmp_path / "dst"


def test_a_first_mirror_copies_everything(tree):
    source, target = tree
    report = mirror.mirror(source, target, backend="builtin")
    assert (report.copied, report.updated, report.deleted) == (3, 0, 0)
    assert (target / "nested" / "b.txt").read_text(encoding="utf-8") == "b"


def test_a_second_mirror_copies_nothing(tree):
    source, target = tree
    mirror.mirror(source, target, backend="builtin")
    report = mirror.mirror(source, target, backend="builtin")
    assert (report.copied, report.updated, report.skipped) == (0, 0, 3)


def test_changed_files_are_updated_not_recopied(tree):
    source, target = tree
    mirror.mirror(source, target, backend="builtin")
    (source / "a.txt").write_text("much longer contents", encoding="utf-8")
    report = mirror.mirror(source, target, backend="builtin")
    assert (report.copied, report.updated) == (0, 1)
    assert (target / "a.txt").read_text(encoding="utf-8") == "much longer contents"


def test_delete_removes_what_the_source_no_longer_has(tree):
    source, target = tree
    mirror.mirror(source, target, backend="builtin")
    (source / "a.txt").unlink()
    report = mirror.mirror(source, target, delete=True, backend="builtin")
    assert report.deleted == 1
    assert not (target / "a.txt").exists()


def test_without_delete_the_extra_file_stays(tree):
    source, target = tree
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")
    mirror.mirror(source, target, delete=False, backend="builtin")
    assert (target / "keep.txt").exists()


def test_excluded_paths_are_neither_copied_nor_deleted(tree):
    source, target = tree
    report = mirror.mirror(source, target, exclude=("*.log", "nested"), backend="builtin")
    assert report.copied == 1
    assert not (target / "notes.log").exists()
    assert not (target / "nested").exists()


def test_a_dry_run_reports_without_touching_anything(tree):
    source, target = tree
    report = mirror.mirror(source, target, dry_run=True, backend="builtin")
    assert report.copied == 3
    assert not target.exists()
    assert "would copy 3" in str(report)


def test_a_missing_source_is_an_error(tmp_path):
    with pytest.raises(mirror.MirrorError, match="does not exist"):
        mirror.mirror(tmp_path / "nope", tmp_path / "dst", backend="builtin")


# ── choosing a backend ───────────────────────────────────────────────────


def test_remote_targets_are_recognised_and_drive_letters_are_not():
    assert mirror.is_remote("user@host:/srv/profiles")
    assert mirror.is_remote("nas:/volume1/profiles")
    assert mirror.is_remote("rsync://host/module")
    assert not mirror.is_remote("C:\\Users\\karol\\profiles")
    assert not mirror.is_remote("/mnt/backup")


def test_a_remote_target_refuses_the_builtin_backend(tmp_path):
    with pytest.raises(mirror.MirrorError, match="remote target"):
        mirror.mirror(tmp_path, "user@host:/srv", backend="builtin")


def test_a_remote_target_without_rsync_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(mirror, "rsync_binary", lambda: "")
    with pytest.raises(mirror.MirrorError, match="rsync is not installed"):
        mirror.mirror(tmp_path, "user@host:/srv")


def test_a_local_target_falls_back_to_the_builtin_walker(tree, monkeypatch):
    source, target = tree
    monkeypatch.setattr(mirror, "rsync_binary", lambda: "")
    assert mirror.mirror(source, target).backend == "builtin"


def test_the_rsync_command_says_contents_not_directory(monkeypatch):
    monkeypatch.setattr(mirror, "rsync_binary", lambda: "/usr/bin/rsync")
    argv = mirror._rsync_argv("/src", "host:/dst", True, True, ("*.log",))
    assert argv == [
        "/usr/bin/rsync",
        "-a",
        "--itemize-changes",
        "--delete",
        "--dry-run",
        "--exclude",
        "*.log",
        "/src/",
        "host:/dst",
    ]


def test_itemised_output_is_counted():
    output = (
        ">f+++++++++ new.txt\n"
        ">f..t...... changed.txt\n"
        "*deleting   gone.txt\n"
        "cd+++++++++ a-directory/\n"
    )
    assert mirror._parse_itemized(output) == (1, 1, 1)


def test_an_unknown_backend_is_refused(tmp_path):
    with pytest.raises(mirror.MirrorError, match="unknown backend"):
        mirror.mirror(tmp_path, tmp_path, backend="magic")


# ── the rsync step ───────────────────────────────────────────────────────


def test_an_rsync_step_mirrors_a_directory(tree, write_profile):
    from launcher.cli import main

    source, target = tree
    write_profile(
        "backup",
        "name: Backup\nsteps:\n"
        f"  - {{type: rsync, name: notes, src: {source}, dest: {target}, "
        "delete: true, backend: builtin, exclude: ['*.log']}\n",
    )
    assert main(["run", "Backup"]) == 0
    assert (target / "a.txt").exists()
    assert not (target / "notes.log").exists()


def test_an_rsync_step_needs_a_destination(write_profile):
    from launcher.config import load_profile

    path = write_profile("bad", "name: Bad\nsteps:\n  - {type: rsync, src: /tmp}\n")
    with pytest.raises(ValueError, match="requires 'dest'"):
        load_profile(path)


def test_an_rsync_step_rejects_an_unknown_backend(write_profile):
    from launcher.config import load_profile

    path = write_profile(
        "bad", "name: Bad\nsteps:\n  - {type: rsync, src: /a, dest: /b, backend: magic}\n"
    )
    with pytest.raises(ValueError, match="unknown rsync backend"):
        load_profile(path)


def test_the_dry_run_describes_the_mirror(write_profile):
    from launcher.config import load_profile
    from launcher.executor import describe_step

    path = write_profile(
        "backup", "name: B\nsteps:\n  - {type: rsync, src: /a, dest: /b, delete: true}\n"
    )
    assert describe_step(load_profile(path).steps[0]) == "mirror /a -> /b --delete"


# ── syncing the profiles directory ───────────────────────────────────────


def test_sync_mirror_pushes_the_profiles_directory(tmp_path, write_profile, monkeypatch):
    from launcher import mirror as mirror_mod
    from launcher import sync

    monkeypatch.setattr(mirror_mod, "rsync_binary", lambda: "")
    write_profile("dev", "name: Dev\nsteps: []\n")
    target = tmp_path / "backup"
    report = sync.mirror(str(target))
    assert (target / "dev.yaml").exists()
    assert "copy 1" in report


def test_sync_mirror_pulls_back(tmp_path, profiles_dir, monkeypatch):
    from launcher import mirror as mirror_mod
    from launcher import sync

    monkeypatch.setattr(mirror_mod, "rsync_binary", lambda: "")
    source = tmp_path / "elsewhere"
    source.mkdir()
    (source / "work.yaml").write_text("name: Work\nsteps: []\n", encoding="utf-8")
    sync.mirror(str(source), pull=True)
    assert (profiles_dir / "work.yaml").exists()


def test_sync_mirror_without_a_target_explains_how_to_set_one():
    from launcher import sync

    with pytest.raises(sync.SyncError, match="palaunch config set sync_mirror"):
        sync.mirror()


def test_the_mirror_target_comes_from_settings(monkeypatch):
    from launcher import settings, sync

    settings.save({"sync_mirror": "/mnt/stick"})
    assert sync.mirror_target() == "/mnt/stick"
    assert sync.mirror_target("/elsewhere") == "/elsewhere"

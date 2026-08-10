from __future__ import annotations

import asyncio
import sys

import pytest

from launcher.config import Profile, Step
from launcher.executor import (
    _build_dependencies,
    _expand,
    _filter_steps,
    _is_safe_url,
    describe_step,
    dry_run_profile,
    format_result,
    run_profile,
)

PY = sys.executable


def _cmd(*code: str) -> Step:
    return Step(type="command", detach=False, run=[PY, "-c", *code])


# ── variable expansion ───────────────────────────────────────────────────


def test_expand_uses_the_local_env_not_os_environ():
    assert _expand("$FOO/bin", {"FOO": "/opt"}) == "/opt/bin"
    assert _expand("${FOO}", {"FOO": "x"}) == "x"
    assert _expand("%FOO%", {"FOO": "x"}) == "x"


def test_expand_leaves_unknown_variables_alone():
    assert _expand("$NOPE", {}) == "$NOPE"


def test_expand_handles_none():
    assert _expand(None, {}) is None


# ── url safety ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("url", ["https://a.example", "http://a", "mailto:x@y.z"])
def test_safe_urls_are_allowed(url):
    assert _is_safe_url(url)


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "steam://run/1"]
)
def test_unsafe_url_schemes_are_refused(url):
    assert not _is_safe_url(url)


# ── dependency graph ─────────────────────────────────────────────────────


def test_sequential_steps_depend_on_everything_before_them():
    steps = [Step(type="wait"), Step(type="wait"), Step(type="wait")]
    assert _build_dependencies(steps) == [[], [0], [0, 1]]


def test_parallel_batch_shares_dependencies():
    steps = [
        Step(type="wait"),
        Step(type="wait", parallel=True),
        Step(type="wait", parallel=True),
        Step(type="wait"),
    ]
    # both parallel steps wait only for step 0; the trailing step is a barrier
    assert _build_dependencies(steps) == [[], [0], [0], [0, 1, 2]]


def test_explicit_depends_on_overrides_position():
    steps = [
        Step(type="wait", id="late"),
        Step(type="wait", depends_on=["late"]),
    ]
    assert _build_dependencies(steps) == [[], [0]]


# ── step filtering ───────────────────────────────────────────────────────


def test_only_selects_by_name_id_or_type():
    steps = [Step(type="wait", name="nap"), Step(type="url", id="tab", url="https://x")]
    assert [s.name or s.id for s in _filter_steps(steps, only=["nap"])] == ["nap"]
    assert [s.id for s in _filter_steps(steps, only=["tab"])] == ["tab"]
    assert [s.type for s in _filter_steps(steps, only=["url"])] == ["url"]


def test_skip_removes_matching_steps():
    steps = [Step(type="wait", name="nap"), Step(type="wait", name="other")]
    assert [s.name for s in _filter_steps(steps, skip=["nap"])] == ["other"]


def test_filtering_drops_dangling_dependencies():
    steps = [Step(type="wait", id="a", name="a"), Step(type="wait", name="b", depends_on=["a"])]
    kept = _filter_steps(steps, only=["b"])
    assert kept[0].depends_on == []


# ── running ──────────────────────────────────────────────────────────────


def test_run_profile_executes_steps_in_order(no_notifications):
    profile = Profile(name="P", steps=[_cmd("print(1)"), _cmd("print(2)")])
    results = run_profile(profile)
    assert [r.ok for r in results] == [True, True]
    assert all(r.duration >= 0 for r in results)


def test_failing_step_is_reported_but_does_not_stop_the_run(no_notifications):
    profile = Profile(
        name="P",
        steps=[_cmd("raise SystemExit(3)"), Step(type="wait", seconds=0, name="after")],
    )
    results = run_profile(profile)
    assert results[0].ok is False and "exit 3" in results[0].detail
    assert results[1].ok is True


def test_optional_failure_does_not_count(no_notifications):
    step = _cmd("raise SystemExit(1)")
    step.optional = True
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is False
    assert results[0].counts_as_failure is False


def test_retries_are_attempted_and_reported(no_notifications, tmp_path):
    counter = tmp_path / "n"
    code = (
        f"import pathlib;p=pathlib.Path({str(counter)!r});"
        "n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1));"
        "raise SystemExit(0 if n>=2 else 1)"
    )
    step = Step(type="command", detach=False, run=[PY, "-c", code], retries=3)
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is True
    assert results[0].attempts == 3


def test_disabled_step_is_skipped(no_notifications):
    step = Step(type="wait", seconds=5, enabled=False)
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].skipped and results[0].detail == "disabled"


def test_when_condition_skips_the_step(no_notifications):
    step = Step(type="wait", seconds=5, when={"platform": ["plan9"]})
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].skipped and "condition not met" in results[0].detail


def test_env_step_is_visible_to_later_steps(no_notifications):
    profile = Profile(
        name="P",
        steps=[
            Step(type="env", set={"PAL_TEST_VALUE": "42"}),
            _cmd("import os;print(os.environ['PAL_TEST_VALUE'])"),
        ],
    )
    results = run_profile(profile)
    assert results[1].ok and "42" in results[1].detail


def test_interpolation_reaches_command_arguments(no_notifications):
    profile = Profile(
        name="Interp",
        vars={"who": "world"},
        steps=[_cmd("print('hi {{ vars.who }} from {{ profile }}')")],
    )
    results = run_profile(profile)
    assert "hi world from Interp" in results[0].detail


def test_explicit_dependency_failure_skips_dependents(no_notifications):
    steps = [
        Step(type="command", id="boom", detach=False, run=[PY, "-c", "raise SystemExit(1)"]),
        Step(type="wait", seconds=0, name="after", depends_on=["boom"]),
    ]
    results = run_profile(Profile(name="P", steps=steps))
    after = next(r for r in results if r.step.name == "after")
    assert after.skipped and "boom" in after.detail


def test_implicit_ordering_does_not_cascade_failures(no_notifications):
    steps = [
        Step(type="command", detach=False, run=[PY, "-c", "raise SystemExit(1)"]),
        Step(type="wait", seconds=0, name="after"),
    ]
    results = run_profile(Profile(name="P", steps=steps))
    assert next(r for r in results if r.step.name == "after").ok is True


def test_on_failure_steps_run_after_a_failure(no_notifications, tmp_path):
    marker = tmp_path / "healed.txt"
    recovery = Step(type="file", action="write", dest=str(marker), content="ok")
    step = Step(
        type="command",
        detach=False,
        run=[PY, "-c", "raise SystemExit(1)"],
        on_failure=[recovery],
    )
    results = run_profile(Profile(name="P", steps=[step]))
    main = next(r for r in results if r.step.type == "command")
    assert marker.read_text() == "ok"
    assert "on_failure: 1/1 ok" in main.detail


def test_timeout_kills_a_blocking_command(no_notifications):
    step = Step(
        type="command", detach=False, timeout=0.5, run=[PY, "-c", "import time;time.sleep(30)"]
    )
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is False and "timed out" in results[0].detail


def test_parallel_steps_actually_overlap(no_notifications):
    sleeper = "import time;time.sleep(0.6)"
    steps = [
        Step(type="command", parallel=True, detach=False, run=[PY, "-c", sleeper]),
        Step(type="command", parallel=True, detach=False, run=[PY, "-c", sleeper]),
    ]
    import time

    started = time.monotonic()
    results = run_profile(Profile(name="P", steps=steps))
    elapsed = time.monotonic() - started
    assert all(r.ok for r in results)
    assert elapsed < 1.2, "parallel steps ran sequentially"


def test_dependency_cycle_is_reported_not_hung(no_notifications):
    a = Step(type="wait", id="a", name="a", depends_on=["b"])
    b = Step(type="wait", id="b", name="b", depends_on=["a"])
    results = run_profile(Profile(name="P", steps=[a, b]))
    assert all(not r.ok for r in results)
    assert all("cycle" in r.detail for r in results)


def test_nested_profile_step_runs_the_other_profile(no_notifications, write_profile, tmp_path):
    marker = tmp_path / "nested.txt"
    write_profile(
        "inner",
        f"""
name: Inner
steps:
  - {{type: file, action: write, dest: {str(marker)!r}, content: "from inner"}}
""",
    )
    results = run_profile(Profile(name="Outer", steps=[Step(type="profile", profile="Inner")]))
    assert results[0].ok and marker.read_text() == "from inner"


def test_nested_profile_cycle_is_refused(no_notifications, write_profile):
    write_profile("loop", "name: Loop\nsteps:\n  - {type: profile, profile: Loop}\n")
    from launcher.config import find_profile

    results = run_profile(find_profile("Loop"))
    assert results[0].ok is False and "cycle" in results[0].detail


def test_unknown_secret_fails_the_step_with_a_readable_message(no_notifications, monkeypatch):
    from launcher import secrets

    monkeypatch.setattr(
        secrets,
        "get",
        lambda name: (_ for _ in ()).throw(secrets.SecretError(f"secret '{name}' is not set")),
    )
    step = Step(type="url", url="https://example.com/{{ secret.token }}")
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is False and "not set" in results[0].detail


def test_secret_values_are_scrubbed_from_details(no_notifications, monkeypatch):
    from launcher import secrets

    monkeypatch.setattr(secrets, "get", lambda _name: "hunter2")
    step = _cmd("print('token={{ secret.token }}')")
    results = run_profile(Profile(name="P", steps=[step]))
    assert "hunter2" not in results[0].detail
    assert "***" in results[0].detail


# ── file steps ───────────────────────────────────────────────────────────


def test_file_copy_and_symlink_and_remove(no_notifications, tmp_path):
    source = tmp_path / "src.txt"
    source.write_text("payload")
    copy = tmp_path / "nested" / "copy.txt"
    link = tmp_path / "link.txt"
    steps = [
        Step(type="file", action="copy", src=str(source), dest=str(copy)),
        Step(type="file", action="symlink", src=str(source), dest=str(link)),
        Step(type="file", action="append", dest=str(copy), content="!"),
    ]
    results = run_profile(Profile(name="P", steps=steps))
    assert all(r.ok for r in results), [r.detail for r in results]
    assert copy.read_text() == "payload!"
    assert link.is_symlink()

    removal = run_profile(
        Profile(name="P", steps=[Step(type="file", action="remove", dest=str(copy))])
    )
    assert removal[0].ok and not copy.exists()


def test_file_symlink_replaces_an_existing_link(no_notifications, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_text("1")
    second.write_text("2")
    link = tmp_path / "link"
    link.symlink_to(first)
    results = run_profile(
        Profile(
            name="P", steps=[Step(type="file", action="symlink", src=str(second), dest=str(link))]
        )
    )
    assert results[0].ok and link.resolve() == second.resolve()


def test_file_step_reports_a_missing_source(no_notifications, tmp_path):
    step = Step(type="file", action="copy", src=str(tmp_path / "ghost"), dest=str(tmp_path / "x"))
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is False and "does not exist" in results[0].detail


# ── script steps ─────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="bash is not the default shell on Windows")
def test_script_step_runs_inline_shell(no_notifications):
    step = Step(type="script", shell="bash", script="echo hello from {{ profile }}\nexit 0\n")
    results = run_profile(Profile(name="Scripted", steps=[step]))
    assert results[0].ok and "hello from Scripted" in results[0].detail


@pytest.mark.skipif(sys.platform == "win32", reason="bash is not the default shell on Windows")
def test_failing_script_reports_the_exit_code(no_notifications):
    results = run_profile(
        Profile(name="P", steps=[Step(type="script", shell="bash", script="exit 7\n")])
    )
    assert results[0].ok is False and "exit 7" in results[0].detail


# ── wait_for ─────────────────────────────────────────────────────────────


def test_wait_for_rejects_a_non_probe_url(no_notifications):
    step = Step(type="wait_for", url="ftp://example.com", timeout=1)
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is False and "must be tcp:// or http(s)://" in results[0].detail


def test_wait_for_times_out_on_a_dead_port(no_notifications):
    step = Step(type="wait_for", url="tcp://127.0.0.1:1", timeout=1, interval=0.2)
    results = run_profile(Profile(name="P", steps=[step]))
    assert results[0].ok is False and "not ready" in results[0].detail


def test_wait_for_succeeds_against_a_live_socket(no_notifications):
    import socket
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    threading.Thread(target=lambda: server.accept(), daemon=True).start()
    try:
        step = Step(type="wait_for", url=f"tcp://127.0.0.1:{port}", timeout=3)
        results = run_profile(Profile(name="P", steps=[step]))
        assert results[0].ok and "is ready" in results[0].detail
    finally:
        server.close()


# ── dry run and formatting ───────────────────────────────────────────────


def test_dry_run_executes_nothing(tmp_path):
    target = tmp_path / "must-not-exist"
    profile = Profile(
        name="P", steps=[Step(type="file", action="write", dest=str(target), content="x")]
    )
    results = dry_run_profile(profile)
    assert results[0].detail.startswith("would ")
    assert not target.exists()


def test_dry_run_mentions_conditions_and_dependencies():
    step = Step(type="wait", seconds=1, when={"platform": "linux"}, depends_on=["x"])
    detail = dry_run_profile(Profile(name="P", steps=[step]))[0].detail
    assert "when:" in detail and "after: x" in detail


@pytest.mark.parametrize(
    "step, expected",
    [
        (Step(type="app", path="/bin/x"), "launch /bin/x"),
        (Step(type="url", url="https://x"), "open https://x"),
        (Step(type="kill", process="p"), "kill process p"),
        (Step(type="profile", profile="Other"), "nested profile 'Other'"),
        (Step(type="http", method="POST", url="https://x"), "POST https://x"),
    ],
)
def test_describe_step(step, expected):
    assert expected in describe_step(step)


def test_format_result_marks_optional_failures_differently():
    from launcher.executor import StepResult

    hard = StepResult(Step(type="wait", name="a"), False, "boom")
    soft = StepResult(Step(type="wait", name="a", optional=True), False, "boom")
    assert format_result(hard).startswith("[ERR]")
    assert format_result(soft).startswith("[err]")


def test_run_profile_appends_history(no_notifications):
    from launcher.state import load_history

    run_profile(Profile(name="Hist", steps=[Step(type="wait", seconds=0)]))
    records = load_history(limit=5)
    assert records[0]["profile"] == "Hist"
    assert records[0]["steps"][0]["type"] == "wait"


def test_async_entry_point_is_reusable(no_notifications):
    from launcher.executor import run_profile_async

    profile = Profile(name="P", steps=[Step(type="wait", seconds=0)])
    results = asyncio.run(run_profile_async(profile))
    assert results[0].ok

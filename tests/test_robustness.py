"""Running alongside other commands, being stopped, and what is left stale.

The project lock, SIGTERM handling, the warning `run` gives about recipes
downstream of it, and what `sync` leaves when it stops partway.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import useful_views as uvs
from useful_views.lock import project_lock

from conftest import add_raw_csv, make_views_step, write, write_recipe


def cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "useful_views", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8")


def chain(root: Path) -> None:
    """a reads raw data; b and c each read a's view. All three recorded."""
    raw = add_raw_csv(root)
    write_recipe(root, "a", inputs=[raw], outputs=["useful-views/a.parquet"])
    write_recipe(root, "b", inputs=["useful-views/a.parquet"], outputs=["useful-views/b.parquet"])
    write_recipe(root, "c", inputs=["useful-views/a.parquet"], outputs=["useful-views/c.parquet"])
    uvs.sync(root=root)


def change_a(root: Path) -> None:
    """Change a's record and its data. sync then runs a, and b and c after it:
    with the same data, they would be skipped as still up to date."""
    write_recipe(root, "a", inputs=["raw/data.csv"], outputs=["useful-views/a.parquet"],
                 description="Test recipe a, changed.", steps={"01_make.py": (
                     "# Write other data.\nimport pyarrow as pa, pyarrow.parquet as pq\n"
                     "pq.write_table(pa.table({'id': [7, 8, 9]}), 'useful-views/a.parquet')\n")})


# --- the project lock -----------------------------------------------------------


HOLD_LOCK = (
    "import os, sys\n"
    "from pathlib import Path\n"
    "from useful_views.lock import project_lock\n"
    "with project_lock(Path(sys.argv[1])):\n"
    "    print('locked', os.getpid(), flush=True)\n"
    "    sys.stdin.read()\n"
)


@pytest.fixture
def held(project):
    """Another process holding the project's lock until the test ends. Its
    `holder` is the process number the lock records (on Windows, a venv's
    python.exe launcher has a number of its own)."""
    proc = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(project)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    word, pid = proc.stdout.readline().split()
    assert word == "locked"
    proc.holder = int(pid)
    yield proc
    proc.stdin.close()
    proc.wait(timeout=30)


def test_a_second_command_is_refused_while_another_changes_the_project(project, held):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    for call in (lambda: uvs.run("a", root=project), lambda: uvs.sync(root=project),
                 lambda: uvs.cookbook(root=project)):
        with pytest.raises(uvs.ProjectError, match="Another useful-views command is already changing") as err:
            call()
        assert f"process {held.holder}" in str(err.value)
    assert not (project / "useful-views" / "a.parquet").exists()
    out = cli("sync", "--json", cwd=project)
    assert out.returncode == 3 and json.loads(out.stdout)["error"]["type"] == "ProjectError"


def test_reading_commands_do_not_wait_for_the_lock(project, held):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    assert [f.kind for f in uvs.check(root=project)] == ["never-run"]
    assert not uvs.cookbook(root=project, check=True).up_to_date


def test_the_lock_is_released_when_the_command_ends(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    uvs.run("a", root=project)
    uvs.sync(root=project, all=True)  # would be refused if run had kept it
    with pytest.raises(uvs.StepFailed):
        write(project, "recipes/a/01_make.py", "# Fail.\nraise SystemExit('boom')\n")
        uvs.run("a", root=project)
    write(project, "recipes/a/01_make.py", make_views_step(["useful-views/a.parquet"]))
    uvs.run("a", root=project)  # released after a failure too


def test_the_lock_is_taken_once_even_while_waiting(project, monkeypatch):
    """Windows refuses a second lock on a region the same handle holds, so
    taking the lock must try each time only until it succeeds."""
    from useful_views import lock

    calls = []

    def try_lock(fd):
        calls.append(fd)
        if len(calls) > 1:
            raise AssertionError("tried to lock again after succeeding")
        return True

    monkeypatch.setattr(lock, "_try_lock", try_lock)
    monkeypatch.setattr(lock, "WAIT_SECONDS", 2.0)
    with project_lock(project):
        pass
    assert len(calls) == 1


def test_a_lock_being_released_is_waited_for(project, monkeypatch):
    from useful_views import lock

    answers = iter([False, False, True])
    monkeypatch.setattr(lock, "_try_lock", lambda fd: next(answers))
    monkeypatch.setattr(lock, "WAIT_SECONDS", 2.0)
    with project_lock(project):
        pass


def test_the_lock_is_reentrant_in_one_thread(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    with project_lock(project):
        uvs.run("a", root=project)
        uvs.sync(root=project)


def test_a_lock_left_by_a_killed_process_does_not_block(project):
    proc = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, str(project)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    word, holder = proc.stdout.readline().split()
    assert word == "locked"
    # Kill the process that holds the lock. On Windows, a venv's python.exe is
    # a launcher that starts the real interpreter as a process of its own.
    os.kill(int(holder), getattr(signal, "SIGKILL", signal.SIGTERM))
    proc.wait(timeout=30)
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"])
    uvs.sync(root=project)


# --- being stopped by a signal ------------------------------------------------------


SLOW_STEP = (
    "# Write this process's number, then wait to be stopped.\n"
    "import os, time\n"
    "open('work/{name}/pid', 'w').write(str(os.getpid()))\n"
    "time.sleep(60)\n"
)


def wait_for(path: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists() or not path.read_text():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.05)


def gone(pid: int, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM cannot be caught on Windows")
def test_sigterm_stops_the_step_and_cleans_up(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"],
                 steps={"01_wait.py": SLOW_STEP.replace("{name}", "a")})
    proc = subprocess.Popen([sys.executable, "-m", "useful_views", "run", "a", "--json"], cwd=project,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wait_for(project / "work" / "a" / "pid")
    step_pid = int((project / "work" / "a" / "pid").read_text())
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == 128 + signal.SIGTERM
    assert "terminated (SIGTERM)" in stderr
    assert json.loads(stdout)["error"]["type"] == "Interrupted"
    assert gone(step_pid), "the step was left running"
    assert not (project / "useful-views" / "a.parquet").exists()
    assert [f.kind for f in uvs.check(root=project)] == ["never-run"]


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM cannot be caught on Windows")
def test_sigterm_during_sync_says_what_was_done(project):
    raw = add_raw_csv(project)
    write_recipe(project, "a", inputs=[raw], outputs=["useful-views/a.parquet"])
    write_recipe(project, "b", inputs=["useful-views/a.parquet"], outputs=["useful-views/b.parquet"],
                 steps={"01_wait.py": SLOW_STEP.replace("{name}", "b")})
    proc = subprocess.Popen([sys.executable, "-m", "useful_views", "sync", "--json"], cwd=project,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    wait_for(project / "work" / "b" / "pid")
    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == 128 + signal.SIGTERM
    error = json.loads(stdout)["error"]
    assert "sync stopped at recipes/b" in error["message"] and "run recipes/a" in error["message"]
    assert error["sync_result"]["ran"] == ["a"]


# --- what run and sync leave stale ----------------------------------------------------


def test_run_names_the_recipes_downstream_of_it(project):
    chain(project)
    change_a(project)
    with pytest.warns(uvs.UsefulViewsWarning, match="recipes/b, recipes/c") as caught:
        result = uvs.run("a", root=project)
    # Their records show they read a's old data, so a plain sync finds them.
    assert any(w.endswith("Bring them up to date with: useful-views sync") for w in result.warnings)
    assert len([w for w in caught if "made its views again" in str(w.message)]) == 1


def test_run_of_the_last_recipe_warns_about_nothing(project):
    chain(project)
    assert uvs.run("c", root=project).warnings == ()


def test_sync_does_not_warn_about_recipes_it_runs_itself(project):
    chain(project)
    result = uvs.sync(root=project, all=True)
    assert not any("made its views again" in w for w in result.warnings)


def test_a_sync_that_stops_removes_views_made_from_views_it_replaced(project):
    chain(project)
    # b's code now fails (its top comment, and so its record, is unchanged);
    # a's record and data have changed, so sync runs a, then b and c, which read it.
    write(project, "recipes/b/01_make.py", "# Write a small table to each output.\nraise SystemExit('boom')\n")
    change_a(project)
    with pytest.raises(uvs.StepFailed) as err:
        uvs.sync(root=project)
    message = str(err.value)
    assert "sync stopped at recipes/b" in message and "It had not yet run: recipes/c" in message
    assert "The views of recipes/c were made from views this sync had already made again" in message
    assert message.rstrip().endswith("Fix the problem above, then run: useful-views sync")
    assert "then run: useful-views run" not in message  # inside sync, sync gives the next command
    assert err.value.sync_result.ran == ("a",)
    assert not (project / "useful-views" / "c.parquet").exists()
    kinds = {f.recipe: f.kind for f in uvs.check(root=project)}
    assert kinds == {"b": "missing-view", "c": "missing-view"}
    write(project, "recipes/b/01_make.py", make_views_step(["useful-views/b.parquet"]))
    assert uvs.sync(root=project).ran == ("b", "c")


def test_a_failed_sync_all_says_to_run_sync_all_again(project):
    chain(project)
    write(project, "recipes/a/01_make.py", "# Write a small table to each output.\nraise SystemExit('boom')\n")
    with pytest.raises(uvs.StepFailed) as err:
        uvs.sync(root=project, all=True)
    assert str(err.value).rstrip().endswith("then run: useful-views sync --all")


def test_run_names_the_command_to_run_again(project):
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=["useful-views/a.parquet"],
                 steps={"01_fail.py": "# Fail.\nraise SystemExit('boom')\n"})
    with pytest.raises(uvs.StepFailed) as err:
        uvs.run("a", root=project)
    assert "Fix the problem in that file, then run: useful-views run a." in str(err.value)


# --- what a failed step's message says ----------------------------------------------

from useful_views import runner  # noqa: E402
from useful_views.recipe import Step  # noqa: E402


def test_progress_bars_do_not_flood_the_message(project):
    bar = "".join(f"\r{i:>6}/100000 [{'#' * (i // 2000)}]" for i in range(0, 100000, 7))
    stderr = bar + "\nTraceback (most recent call last):\nValueError: bad value\n"
    tail = runner._tail(stderr)
    assert tail.endswith("ValueError: bad value") and len(tail) < 2000
    assert "99995/100000" in tail and "\r" not in tail  # only the bar's last state


def test_the_tail_is_bounded_however_long_the_lines(project):
    tail = runner._tail("x" * 1_000_000 + "\n" + "\n".join(f"line {i} " + "y" * 900 for i in range(60)))
    assert len(tail) <= runner.TAIL_CHARS + 100 and tail.splitlines()[-1].startswith("line 59")


def test_a_step_keeps_only_the_end_of_a_huge_error_output(project):
    write(project, "recipes/a/01_noisy.py",
          "# Write a lot to standard error.\nimport sys\nsys.stderr.write('z' * 500000 + '\\nthe end\\n')\n")
    code, stderr = runner.run_step(project, Step("noisy", "recipes/a/01_noisy.py", "Write a lot."))
    stderr = stderr.replace("\r\n", "\n")  # Windows writes CRLF
    assert code == 0 and stderr.endswith("the end\n") and len(stderr) < runner.STDERR_KEEP + 200
    assert "earlier bytes of error output were not kept" in stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_a_step_killed_by_a_signal_is_named(project):
    step = Step("die", "recipes/a/01_die.py", "Die.")
    failure = runner._step_failure(step, -9, "", "a", project, "useful-views run a")
    assert "was stopped by SIGKILL" in str(failure) and "too much memory" in str(failure)
    assert "exit code -9" not in str(failure)
    assert "was stopped by SIGTERM" in str(runner._step_failure(step, -15, "", "a", project))


def test_the_hint_for_a_helper_module_does_not_suggest_installing_it(project):
    write(project, "helpers.py", "X = 1\n")
    hint = runner._missing_module_hint(project, "helpers")
    assert "your own code (helpers.py)" in hint
    assert "pip install" not in hint and "uv add" not in hint
    write(project, "recipes/b/utils.py", "Y = 2\n")
    assert "recipes/b/utils.py" in runner._missing_module_hint(project, "utils")


def test_the_install_hint_fits_the_environment(project, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_isolated_tool", lambda: False)
    monkeypatch.setattr(runner, "_uv_project", lambda: tmp_path / "my project")
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    hint = runner._missing_module_hint(project, "sklearn")
    assert "'scikit-learn'" in hint and "uv add --project" in hint and "scikit-learn" in hint.splitlines()[-2]
    assert "-m pip install" not in hint and "uv pip install --python" in hint
    monkeypatch.setattr(runner, "_uv_project", lambda: None)
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: object())
    hint = runner._missing_module_hint(project, "polars")
    assert "-m pip install polars" in hint and "uv add" not in hint


def test_the_install_hint_explains_an_isolated_tool_environment(project, monkeypatch):
    monkeypatch.setattr(runner, "_isolated_tool", lambda: True)
    hint = runner._missing_module_hint(project, "polars")
    assert "uvx, uv tool or pipx" in hint and "uv add useful-views" in hint


def test_paths_with_spaces_are_quoted_for_the_shell(monkeypatch):
    monkeypatch.setattr(runner, "_windows", lambda: False)
    assert runner._arg("/Users/Jane Doe/python") == "'/Users/Jane Doe/python'"
    assert runner._command("/usr/bin/python3") == "/usr/bin/python3"
    monkeypatch.setattr(runner, "_windows", lambda: True)
    exe = r"C:\Users\Jane Doe\python.exe"
    assert runner._arg(exe) == f'"{exe}"' and runner._command(exe) == f'& "{exe}"'
    assert runner._arg(r"C:\tools\python.exe") == r"C:\tools\python.exe"


def test_the_windows_install_hint_is_valid_powershell(project, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "_windows", lambda: True)
    monkeypatch.setattr(runner, "_isolated_tool", lambda: False)
    monkeypatch.setattr(runner, "_uv_project", lambda: Path(r"C:\Users\Jane Doe\thesis"))
    monkeypatch.setattr(runner.sys, "executable", r"C:\Users\Jane Doe\thesis\.venv\Scripts\python.exe")
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: object())
    lines = [line.strip() for line in runner._missing_module_hint(project, "polars").splitlines()]
    assert 'uv add --project "C:\\Users\\Jane Doe\\thesis" polars' in lines
    assert ('& "C:\\Users\\Jane Doe\\thesis\\.venv\\Scripts\\python.exe" -m pip install polars   (in PowerShell)'
            in lines)
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None)
    lines = [line.strip() for line in runner._missing_module_hint(project, "polars").splitlines()]
    assert 'uv pip install --python "C:\\Users\\Jane Doe\\thesis\\.venv\\Scripts\\python.exe" polars' in lines


def test_an_interrupt_while_the_output_relay_starts_still_stops_the_step(project, monkeypatch):
    """Thread.start() waits for the new thread; a signal arriving then must not
    leave the step running on its own (seen under load in CI-like runs)."""
    import threading
    import types

    write(project, "recipes/a/01_wait.py", "# Wait.\nimport time\ntime.sleep(60)\n")
    started = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        started.append(proc)
        return proc

    class Interrupted(threading.Thread):
        def start(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(runner.subprocess, "Popen", popen)
    monkeypatch.setattr(runner, "threading", types.SimpleNamespace(Thread=Interrupted))
    with pytest.raises(KeyboardInterrupt):
        runner.run_step(project, Step("wait", "recipes/a/01_wait.py", "Wait."))
    assert started and started[0].returncode is not None  # killed, and waited for


# --- review of the fixes: what a stopped sync leaves, and what it may delete --------

from useful_views import syncing  # noqa: E402


def test_a_record_naming_a_folder_outside_work_is_refused_and_nothing_is_deleted(project, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious")
    data = json.loads((project / "datapackage.json").read_text())
    for name in ("../../victim", str(victim), "", ".."):
        data["recipes"] = [{"name": name, "description": "x", "path": "recipes/x", "inputs": [], "outputs": [],
                            "steps": []}]
        (project / "datapackage.json").write_text(json.dumps(data))
        with pytest.raises(uvs.ProjectError, match="never writes"):
            uvs.sync(root=project)
    assert (victim / "keep.txt").read_text() == "precious"
    data["recipes"] = [{"name": "x", "description": "x", "path": "recipes/x", "inputs": [],
                        "outputs": ["useful-views/../datapackage.json"], "steps": []}]
    (project / "datapackage.json").write_text(json.dumps(data))
    with pytest.raises(uvs.ProjectError, match="output 'useful-views/../datapackage.json'"):
        uvs.check(root=project)


def test_a_sync_interrupted_before_a_recipe_starts_still_removes_its_stale_views(project, monkeypatch):
    chain(project)
    change_a(project)
    real_run = syncing._run

    def run(project_, name, **kwargs):
        if name == "b":  # as if Ctrl-C came while b's inputs were being checked
            raise KeyboardInterrupt
        return real_run(project_, name, **kwargs)

    monkeypatch.setattr(syncing, "_run", run)
    with pytest.raises(KeyboardInterrupt) as err:
        uvs.sync(root=project)
    assert "sync stopped at recipes/b" in str(err.value) and "To finish, run: useful-views sync" in str(err.value)
    assert not (project / "useful-views" / "b.parquet").exists() and not (project / "useful-views" / "c.parquet").exists()
    monkeypatch.setattr(syncing, "_run", real_run)
    assert uvs.sync(root=project).ran == ("b", "c")


def test_a_stopped_sync_all_removes_the_views_it_left_stale(project):
    chain(project)
    write(project, "recipes/b/01_make.py", "# Write a small table to each output.\nraise SystemExit('boom')\n")
    with pytest.raises(uvs.StepFailed):
        uvs.sync(root=project, all=True)
    assert not (project / "useful-views" / "c.parquet").exists()  # c reads a, which ran again
    write(project, "recipes/b/01_make.py", make_views_step(["useful-views/b.parquet"]))
    assert uvs.sync(root=project).ran == ("b", "c")


def test_warnings_made_errors_do_not_stop_sync_halfway(project):
    raw = add_raw_csv(project)
    step = make_views_step(["useful-views/a.parquet"]) + "open('raw/data.csv', 'a').write('3,4.5\\n')\n"
    write_recipe(project, "a", inputs=[raw], outputs=["useful-views/a.parquet"], steps={"01_make.py": step})
    write_recipe(project, "b", inputs=["useful-views/a.parquet"], outputs=["useful-views/b.parquet"])
    code = ("import sys, useful_views as uvs\n"
            "try:\n    uvs.sync(root=sys.argv[1])\nexcept uvs.UsefulViewsWarning as w:\n    print('raised:', w)\n")
    out = subprocess.run([sys.executable, "-W", "error::UserWarning", "-c", code, str(project)],
                         capture_output=True, text=True)
    assert "raised:" in out.stdout and "changed raw/data.csv" in out.stdout, out.stderr
    names = [r["name"] for r in json.loads((project / "datapackage.json").read_text())["recipes"]]
    assert names == ["a", "b"]  # sync finished first, then raised


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_nohup_still_protects_a_command_from_hangups(project):
    step = "# Wait, then write the view.\nimport time\ntime.sleep(2)\n" + make_views_step([VIEW_A]).split("\n", 1)[1]
    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=[VIEW_A], steps={"01_make.py": step})
    proc = subprocess.Popen([sys.executable, "-m", "useful_views", "run", "a"], cwd=project,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            preexec_fn=lambda: signal.signal(signal.SIGHUP, signal.SIG_IGN))
    time.sleep(0.8)
    proc.send_signal(signal.SIGHUP)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    assert (project / VIEW_A).is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_sync_deletes_a_removed_recipes_read_only_work_folder(project):
    import shutil as sh
    import stat

    write_recipe(project, "a", inputs=[add_raw_csv(project)], outputs=[VIEW_A])
    uvs.sync(root=project)
    locked = project / "work" / "a" / "sub"
    locked.mkdir()
    (locked / "f.txt").write_text("x")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    sh.rmtree(project / "recipes" / "a")
    result = uvs.sync(root=project)
    assert "work/a/" in result.deleted and not (project / "work" / "a").exists()


VIEW_A = "useful-views/a.parquet"

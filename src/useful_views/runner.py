"""Running one recipe ("What run does" in the README)."""

from __future__ import annotations

import codecs
import importlib.util
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa

from .errors import OutputError, ProjectError, RecipeRefused, StepFailed, warn
from .lock import project_lock
from .paths import CATALOG, DATAPACKAGE, RECIPES, VIEWS, WORK, exists_exact
from .project import find_root
from .recipe import Step, load_recipe, resolve_name
from .record import assemble, describe_view, load_datapackage, retry, split_resources, write_generated
from .rules import check_all

STDERR_TAIL = 50  # lines of a failed step's error output shown in the message
TAIL_CHARS = 8000  # at most this many characters of them
LINE_CHARS = 1000  # and of each line
STDERR_KEEP = 64 * 1024  # bytes of a step's error output kept, from the end
POLL_SECONDS = 0.2
RELAY_GRACE_SECONDS = 2.0

# Import names whose package has a different name on PyPI.
PACKAGE_FOR_MODULE = {
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
}


@dataclass(frozen=True)
class RunResult:
    """What `run` did.

    Attributes:
        recipe: The recipe that ran.
        outputs: The views it wrote, as paths from the project root.
        warnings: Warnings issued along the way (also shown as they happen).
    """

    recipe: str
    outputs: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"recipe": self.recipe, "outputs": list(self.outputs), "warnings": list(self.warnings)}


def _write(sink, text: str) -> None:
    """Best effort: a stream that cannot take the text must never stop the
    relay, or the step would block on a full pipe. `sink` is a stream, or the
    name of one in sys ("stdout", "stderr"), looked up now."""
    if isinstance(sink, str):
        sink = getattr(sys, sink)
    if sink is None or not text:
        return
    try:
        sink.write(text)
    except UnicodeEncodeError:
        encoding = getattr(sink, "encoding", None) or "utf-8"
        try:
            sink.write(text.encode(encoding, "backslashreplace").decode(encoding))
        except Exception:
            return
    except Exception:
        return  # closed, or not a text stream
    try:
        sink.flush()
    except Exception:
        pass


class _Tail:
    """The last STDERR_KEEP bytes of a stream, and how many came before."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.dropped = 0

    def append(self, chunk: bytes) -> None:
        self.data += chunk
        extra = len(self.data) - STDERR_KEEP
        if extra > 0:
            del self.data[:extra]
            self.dropped += extra

    def text(self) -> str:
        text = self.data.decode("utf-8", errors="replace")
        if self.dropped:
            text = f"({self.dropped} earlier bytes of error output were not kept)\n" + text
        return text


def _relay(stream, sink, keep: _Tail | None) -> None:
    """Copy a child's output to our sys.stdout/sys.stderr as it arrives, so it
    shows in a terminal and in a notebook cell alike."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while chunk := stream.read1(65536):
            if keep is not None:
                keep.append(chunk)
            _write(sink, decoder.decode(chunk))
        _write(sink, decoder.decode(b"", final=True))
    except (ValueError, OSError):
        pass  # the pipe was closed under us after the step ended


def run_step(root: Path, step: Step) -> tuple[int, str]:
    """Run one step file as its own process. Returns exit code and stderr."""
    # UTF-8 mode, so open() reads and writes UTF-8 by default on Windows too,
    # and a step gives the same bytes on every system.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    proc = subprocess.Popen(
        [sys.executable, step.path],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    err = _Tail()
    threads = [
        # The streams are chosen now, so output from a process the step leaves
        # running never lands somewhere a later caller redirected stdout to.
        threading.Thread(target=_relay, args=(proc.stdout, sys.stdout, None), daemon=True),
        threading.Thread(target=_relay, args=(proc.stderr, sys.stderr, err), daemon=True),
    ]
    try:
        # Everything after Popen is inside the try, starting the threads too:
        # Thread.start() waits for the thread, and an interrupt arriving then
        # must still stop the step.
        for t in threads:
            t.start()
        # Poll rather than block, so an interrupt (Ctrl-C, or "Interrupt
        # kernel" in Jupyter) is noticed while the step is still running.
        while True:
            try:
                code = proc.wait(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        # A process the step started in the background may still hold the
        # pipes open; do not wait for it.
        # Closing a pipe while its thread is reading would block too, so a
        # thread still reading is left to finish by itself (it is a daemon).
        for t, pipe in zip(threads, (proc.stdout, proc.stderr)):
            if t.ident is not None:  # started
                t.join(RELAY_GRACE_SECONDS)
            if not t.is_alive():
                try:
                    pipe.close()
                except (ValueError, OSError):
                    pass
    return code, err.text()


def _then(again: str | None) -> str:
    """The end of a sentence saying which command to run next, if any."""
    return f", then run: {again}" if again else ""


def _remove_outputs(root: Path, outputs: tuple[str, ...], again: str | None = None) -> None:
    for rel in outputs:
        path = root / rel
        if path.is_file() or path.is_symlink():
            try:
                retry(path.unlink, path)
            except OSError as exc:
                raise ProjectError(
                    f"Could not delete {rel}: {exc.strerror or exc}. Close any program that has it open "
                    f"(a notebook, Excel, a file viewer){_then(again)}."
                ) from exc


def rmtree(folder: Path) -> None:
    """Delete a folder and everything in it, read-only files included (on
    Windows, git and some tools make files read-only)."""

    def make_writable(func, path, exc):
        """Retry a delete that failed on a read-only file or folder."""
        if func not in (os.unlink, os.remove, os.rmdir):
            raise exc if isinstance(exc, BaseException) else exc[1]
        parent = os.path.dirname(path)
        os.chmod(parent, os.stat(parent).st_mode | stat.S_IWUSR | stat.S_IXUSR)
        if not os.path.isdir(path):
            os.chmod(path, os.lstat(path).st_mode | stat.S_IWRITE)
        func(path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=make_writable)
    else:
        shutil.rmtree(folder, onerror=make_writable)


def _empty_work(root: Path, name: str, again: str | None = None) -> None:
    work = root / WORK / name
    try:
        if work.is_dir() and not work.is_symlink():
            rmtree(work)
        elif work.exists() or work.is_symlink():
            work.unlink()
        work.mkdir(parents=True)
    except Exception as exc:
        reason = getattr(exc, "strerror", None) or exc
        raise ProjectError(
            f"Could not empty {WORK}/{name}/: {reason}. Close any program using files in it, check its "
            f"permissions (or delete the folder yourself){_then(again)}."
        ) from exc


def _tail(stderr: str) -> str:
    """The last lines of a step's error output, as a terminal would show them:
    a progress bar that redraws itself with carriage returns is one line, not
    thousands. Bounded, so a message never swamps a terminal or an AI tool."""
    lines = []
    for line in stderr.replace("\r\n", "\n").split("\n"):
        line = line.rstrip("\r").rsplit("\r", 1)[-1]
        lines.append(line if len(line) <= LINE_CHARS else line[:LINE_CHARS] + " [...]")
    while lines and not lines[-1].strip():
        lines.pop()
    if not any(line.strip() for line in lines):
        return "(no error output)"
    tail = "\n".join(lines[-STDERR_TAIL:])
    if len(tail) > TAIL_CHARS:
        tail = "[...]\n" + tail[-TAIL_CHARS:].split("\n", 1)[-1]
    return tail


def _windows() -> bool:
    return os.name == "nt"


def _arg(path: str) -> str:
    """A path as an argument, quoted for this system's usual shell. Double
    quotes work in both PowerShell and the Command Prompt."""
    if _windows():
        return f'"{path}"' if re.search(r"[\s&()'`$;,]", path) else path
    return shlex.quote(path)


def _command(path: str) -> str:
    """A path as the program to run, at the start of a command line. In
    PowerShell a quoted program needs the call operator (&)."""
    quoted = _arg(path)
    return f"& {quoted}" if _windows() and quoted.startswith('"') else quoted


def _local_module(project: Path, module: str) -> str | None:
    """Where the project itself has code by this name, if it does."""
    candidates = [f"{module}.py", module, f"src/{module}", f"src/{module}.py"]
    for rel in candidates:
        if (project / rel).exists():
            return rel
    for folder in sorted((project / RECIPES).glob("*/")):
        if (folder / f"{module}.py").is_file():
            return f"{RECIPES}/{folder.name}/{module}.py"
    return None


def _uv_project() -> Path | None:
    """The uv project whose .venv is running useful-views, if it is one."""
    prefix = Path(sys.prefix)
    if prefix.name == ".venv" and (prefix.parent / "pyproject.toml").is_file():
        return prefix.parent
    return None


def _isolated_tool() -> bool:
    """True when useful-views runs from uvx, uv tool or pipx: an environment
    of its own, which the project's libraries are not in."""
    prefix = Path(sys.prefix).as_posix().lower()
    return any(part in prefix for part in ("/uv/tools/", "/uv/data/tools/", "/pipx/venvs/", "/archive-v0/"))


def _missing_module_hint(project: Path, module: str) -> str:
    local = _local_module(project, module)
    if local:
        return (
            f"\n\n{module!r} is your own code ({local}), not a package to install. A step runs as a script, so "
            "only its own recipe folder is on the import path, not the project root. Put a helper module next to "
            f"the steps that use it ({RECIPES}/<recipe>/{module}.py), or make shared code an installable package "
            "and install it into this environment."
        )
    package = PACKAGE_FOR_MODULE.get(module, module)
    python = sys.executable
    hint = (
        f"\n\nSteps run with the same Python as useful-views: {python}\n"
        f"The module {module!r} is not installed there. Install the package that provides it "
        f"(probably {package!r}) into that environment"
    )
    if _isolated_tool():
        return hint + (
            ". But useful-views is running from an environment of its own (uvx, uv tool or pipx), which your "
            "project's libraries are not in. Install useful-views into your project's environment instead "
            "(uv add useful-views, or pip install useful-views with that environment active) and run it from "
            "there."
        )
    commands = []
    uv_project = _uv_project()
    if uv_project is not None:
        commands.append(f"uv add --project {_arg(str(uv_project))} {package}")
    if importlib.util.find_spec("pip") is not None:
        line = f"{_command(python)} -m pip install {package}"
        commands.append(line + ("   (in PowerShell)" if line.startswith("& ") else ""))
    else:
        commands.append(f"uv pip install --python {_arg(python)} {package}")
    if (Path(sys.prefix) / "conda-meta").is_dir():
        commands.append(f"conda install {package}   (with this environment active)")
    return hint + ", with:\n" + "\n".join(f"  {c}" for c in commands)


def _step_failure(step: Step, code: int, stderr: str, recipe: str, project: Path,
                  again: str | None = None) -> StepFailed:
    if code < 0:  # stopped by a signal (POSIX)
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = f"signal {-code}"
        what = f"was stopped by {name}"
        if -code == getattr(signal, "SIGKILL", 9):
            what += (
                ". This most often means the system stopped it for using too much memory (the out-of-memory "
                "killer, or the memory limit of a cluster job, container or CI runner): process the data in "
                "smaller pieces, or ask for more memory"
            )
    else:
        what = f"failed with exit code {code}"
    message = (
        f"Step {step.path} {what}. Fix the problem in that file{_then(again)}.\n"
        f"Nothing was recorded: {DATAPACKAGE} and {CATALOG} are unchanged, and this recipe's views were removed.\n"
        f"Last lines of the step's error output:\n{_tail(stderr)}"
    )
    missing = re.search(
        r"No module named '([^']+)'|required package '([^']+)' not found|Missing optional dependency '([^']+)'",
        stderr,
    )
    if missing:
        module = next(g for g in missing.groups() if g).split(".")[0]
        message += _missing_module_hint(project, module)
    return StepFailed(message, step=step.path, returncode=code, stderr=stderr, recipe=recipe)


def _actual_name(project: Path, rel: str) -> str | None:
    """The name on disk of a file that differs from `rel` only in letter case
    or in how accented letters are encoded, if there is one."""
    import unicodedata

    folder, _, filename = rel.rpartition("/")
    want = unicodedata.normalize("NFC", filename).casefold()
    try:
        names = os.listdir(project / folder)
    except OSError:
        return None
    for name in names:
        if name != filename and unicodedata.normalize("NFC", name).casefold() == want:
            return f"{folder}/{name}"
    return None


def _check_outputs(project: Path, name: str, loaded) -> dict[str, dict]:
    """Each output must be a non-empty, readable parquet file."""
    bad: list[tuple[str, str]] = []
    for rel in loaded.outputs:
        path = project / rel
        if path.is_dir():
            bad.append((rel, "is a folder, but a view must be a single parquet file (write one file, not a "
                             "partitioned dataset)"))
        elif not exists_exact(project, rel):
            actual = _actual_name(project, rel)
            if actual:
                bad.append((rel, f"was not written: the step wrote {actual}, whose name differs in capital "
                                 "letters or accents; write the name exactly as declared"))
            else:
                bad.append((rel, "was not written"))
        elif not path.is_file():
            bad.append((rel, "was not written"))
        elif path.stat().st_size == 0:
            bad.append((rel, "is empty"))
    views = {}
    if not bad:
        for rel in loaded.outputs:
            try:
                views[rel] = describe_view(project, rel, name, list(loaded.inputs))
            except (pa.ArrowException, OSError) as exc:
                bad.append((rel, f"is not a readable parquet file ({exc})"))
    if bad:
        raise OutputError(
            f"All steps of {loaded.path} ran, but its declared outputs are not right:\n"
            + "\n".join(f"  - {rel} {reason}" for rel, reason in bad)
            + f"\nMake the steps write each output listed in {loaded.path}/recipe.toml, exactly as written there. "
            f"Nothing was recorded: {DATAPACKAGE} and {CATALOG} are unchanged, and this recipe's views were removed.",
            paths=[rel for rel, _ in bad],
            problems=bad,
            recipe=name,
        )
    return views


def _watched(project: Path, loaded, others, data: dict) -> dict[str, tuple[int, int] | None]:
    """Size and modification time of the files a step must not change: every
    declared or recorded raw input, and every other recipe's view."""
    from .paths import RAW, listdir
    from .record import raw_inputs

    paths = {p for p in loaded.inputs if p.startswith(f"{RAW}/")}
    paths |= {p for ins, _ in others.declared.values() for p in ins if p.startswith(f"{RAW}/")}
    paths |= {p for r in data["recipes"] for p in raw_inputs(r)}
    paths |= {f"{VIEWS}/{n}" for n in listdir(project / VIEWS)} - set(loaded.outputs) - {CATALOG}
    found = {}
    for rel in paths:
        try:
            st = os.stat(project / rel)
            found[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            found[rel] = None
    return found


def _changed(project: Path, before: dict, loaded, others, data: dict, collected: list[str]) -> None:
    """Warn about files the steps changed but must not."""
    after = _watched(project, loaded, others, data)
    changed = sorted(rel for rel, stamp in before.items() if after.get(rel) != stamp)
    raw = [rel for rel in changed if not rel.startswith(f"{VIEWS}/")]
    views = [rel for rel in changed if rel.startswith(f"{VIEWS}/")]
    if raw:
        warn(f"{loaded.path} changed {', '.join(raw)} while it ran. Steps must never write to raw/: restore "
             "the original file, and write in-between files to work/ instead.", collected)
    if views:
        warn(f"{loaded.path} changed {', '.join(views)}, which it does not declare as outputs, while it ran. "
             "Each view must be written only by the recipe that declares it; run useful-views sync --all to "
             "make them again.", collected)


def _downstream(others, outputs: tuple[str, ...], data: dict) -> list[str]:
    """Recorded recipes that read these views, directly or through others."""
    found: set[str] = set()
    frontier = set(outputs)
    while frontier:
        readers = {n for n, (ins, _) in others.declared.items() if n not in found and frontier & set(ins)}
        found |= readers
        frontier = {o for n in readers for o in others.declared[n][1]}
    recorded = {r["name"] for r in data["recipes"]}
    return sorted(found & recorded)


def run(recipe: str, *, root: str | Path | None = None) -> RunResult:
    """Run one recipe's steps in order and record what it produced.

    Before anything on disk changes, the recipe is checked against the rules
    (`useful-views rules`). Then its views are deleted and `work/<recipe>/`
    is emptied, each step runs as its own process (from the project root,
    with this Python, its output shown as it runs), every declared view must
    have been written, and the run is recorded in `datapackage.json` and
    `useful-views/README.md`. Other recipes' records are not changed, and no
    other recipe is run: if other recipes read this one's views, a warning
    names them, because their views were made from the old ones (run
    `useful-views sync --all`, or run each of them, upstream first).

    Args:
        recipe: The recipe's name, as in `"pwt_panel"` or `"recipes/pwt_panel/"`.
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.

    Returns:
        A `RunResult` naming the views written.

    Raises:
        RecipeRefused: The recipe breaks a rule; nothing changed.
        StepFailed: A step failed; nothing was recorded and the recipe's views
            were removed.
        OutputError: A declared view was missing, empty or unreadable after
            the steps ran; nothing was recorded.
        ProjectError: Another useful-views command is changing the project,
            or a file could not be written or deleted.
        ProjectNotFound: No project was found.
    """
    project = find_root(root)
    with project_lock(project):
        return _run(project, recipe)


def _run(project: Path, recipe: str, *, in_sync: bool = False) -> RunResult:
    """run() without finding the project or taking its lock. Inside a sync,
    messages leave the next command to sync, and recipes downstream are not
    warned about: sync runs them next."""
    collected: list[str] = []

    # Before anything on disk changes: rules 1 to 8.
    name = resolve_name(project, recipe)
    again = None if in_sync else f"useful-views run {name}"
    try:
        loaded = load_recipe(project, name)
        collected.extend(loaded.warnings)
        data = load_datapackage(project)
        others, raw_described = check_all(project, loaded, data, collected)
    except RecipeRefused as exc:
        exc.recipe = exc.recipe or name
        raise

    # 1. Clear the old views and work files: a leftover can never pass for a fresh result.
    _remove_outputs(project, loaded.outputs, again)
    before = _watched(project, loaded, others, data)
    try:
        _empty_work(project, name, again)
        (project / VIEWS).mkdir(exist_ok=True)

        # 2. Run each step in order; stop at the first that fails.
        try:
            for step in loaded.steps:
                code, stderr = run_step(project, step)
                if code != 0:
                    raise _step_failure(step, code, stderr, name, project, again)
        finally:
            _changed(project, before, loaded, others, data, collected)

        # 3. Every declared view must have been written.
        views = _check_outputs(project, name, loaded)

        # 4. Record the run; only this recipe's files were described again.
        old_views, old_raws = split_resources(data)
        records = [r for r in data["recipes"] if r["name"] != name] + [loaded.record()]
        all_views = {p: r for p, r in old_views.items() if r["producedBy"].get("recipe") != name}
        all_views.update(views)
        all_raws = {**old_raws, **raw_described}
        write_generated(project, assemble(project, records, all_views, all_raws, data.get("name")))
    except BaseException:
        # On any failure from here on, a failed run leaves its views absent.
        try:
            _remove_outputs(project, loaded.outputs)
        except ProjectError:
            pass
        raise

    if not in_sync:
        from .cookbooks import stale_warning

        note = stale_warning(project)
        if note:
            warn(note, collected)
        stale = _downstream(others, loaded.outputs, data)
        if stale:
            warn(
                f"{RECIPES}/{name} made its views again, so the views of the recipes that read them were made "
                f"from the old ones: {', '.join(f'{RECIPES}/{n}' for n in stale)}. sync does not notice this "
                "by itself. Bring them up to date with: useful-views sync --all (or run each of them, upstream "
                "first).",
                collected,
            )
    return RunResult(recipe=name, outputs=loaded.outputs, warnings=tuple(collected))

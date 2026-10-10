"""Running one recipe: rules 9 to 13."""

from __future__ import annotations

import codecs
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa

from .errors import OutputError, ProjectError, StepFailed
from .paths import CATALOG, DATAPACKAGE, VIEWS, WORK, exists_exact
from .project import find_root
from .recipe import Step, load_recipe, resolve_name
from .record import assemble, describe_view, load_datapackage, retry, split_resources, write_generated
from .rules import check_all

STDERR_TAIL = 50
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
    recipe: str
    outputs: tuple[str, ...]
    warnings: tuple[str, ...]


def _write(sink_name: str, text: str) -> None:
    """Best effort: a stream that cannot take the text must never stop the
    relay, or the step would block on a full pipe."""
    sink = getattr(sys, sink_name)
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


def _relay(stream, sink_name: str, keep: list[bytes] | None) -> None:
    """Copy a child's output to our sys.stdout/sys.stderr as it arrives, so it
    shows in a terminal and in a notebook cell alike."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        while chunk := stream.read1(65536):
            if keep is not None:
                keep.append(chunk)
            _write(sink_name, decoder.decode(chunk))
        _write(sink_name, decoder.decode(b"", final=True))
    except (ValueError, OSError):
        pass  # the pipe was closed under us after the step ended


def run_step(root: Path, step: Step) -> tuple[int, str]:
    """Run one step file as its own process. Returns exit code and stderr."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, step.path],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    err: list[bytes] = []
    threads = [
        threading.Thread(target=_relay, args=(proc.stdout, "stdout", None), daemon=True),
        threading.Thread(target=_relay, args=(proc.stderr, "stderr", err), daemon=True),
    ]
    for t in threads:
        t.start()
    try:
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
            t.join(RELAY_GRACE_SECONDS)
            if not t.is_alive():
                try:
                    pipe.close()
                except (ValueError, OSError):
                    pass
    return code, b"".join(err).decode("utf-8", errors="replace")


def _remove_outputs(root: Path, outputs: tuple[str, ...]) -> None:
    for rel in outputs:
        path = root / rel
        if path.is_file() or path.is_symlink():
            try:
                retry(path.unlink, path)
            except OSError as exc:
                raise ProjectError(
                    f"Could not delete {rel}: {exc.strerror or exc}. Close any program that has it open "
                    "(a notebook, Excel, a file viewer), then run the recipe again."
                ) from exc


def _empty_work(root: Path, name: str) -> None:
    work = root / WORK / name

    def make_writable(func, path, exc):
        """Retry a delete that failed on a read-only file or folder."""
        if func not in (os.unlink, os.remove, os.rmdir):
            raise exc if isinstance(exc, BaseException) else exc[1]
        parent = os.path.dirname(path)
        os.chmod(parent, os.stat(parent).st_mode | stat.S_IWUSR | stat.S_IXUSR)
        if not os.path.isdir(path):
            os.chmod(path, os.lstat(path).st_mode | stat.S_IWRITE)
        func(path)

    try:
        if work.is_dir() and not work.is_symlink():
            if sys.version_info >= (3, 12):
                shutil.rmtree(work, onexc=make_writable)
            else:
                shutil.rmtree(work, onerror=make_writable)
        elif work.exists() or work.is_symlink():
            work.unlink()
        work.mkdir(parents=True)
    except Exception as exc:
        reason = getattr(exc, "strerror", None) or exc
        raise ProjectError(
            f"Could not empty {WORK}/{name}/: {reason}. Close any program using files in it, check its "
            "permissions (or delete the folder yourself), then run the recipe again."
        ) from exc


def _step_failure(step: Step, code: int, stderr: str) -> StepFailed:
    tail = "\n".join(stderr.rstrip("\n").split("\n")[-STDERR_TAIL:]) if stderr.strip() else "(no error output)"
    message = (
        f"Step {step.path} failed with exit code {code}. Fix the problem in that file and run the recipe again.\n"
        f"Nothing was recorded: {DATAPACKAGE} and {CATALOG} are unchanged, and this recipe's views were removed.\n"
        f"Last lines of the step's error output:\n{tail}"
    )
    missing = re.search(
        r"No module named '([^']+)'|required package '([^']+)' not found|Missing optional dependency '([^']+)'",
        stderr,
    )
    if missing:
        module = next(g for g in missing.groups() if g).split(".")[0]
        package = PACKAGE_FOR_MODULE.get(module, module)
        message += (
            f"\n\nSteps run with the same Python as useful-views: {sys.executable}\n"
            f"The module {module!r} is not installed there. Install the package that provides it "
            f"(probably {package!r}) into that environment, for example with one of:\n"
            f"  {sys.executable} -m pip install {package}\n"
            f"  uv pip install --python {sys.executable} {package}"
        )
    return StepFailed(message, step=step.path, returncode=code, stderr=stderr)


def _check_outputs(project: Path, name: str, loaded) -> dict[str, dict]:
    """Rule 12: each output is a non-empty, readable parquet file."""
    bad: list[tuple[str, str]] = []
    for rel in loaded.outputs:
        path = project / rel
        if not exists_exact(project, rel) or not path.is_file():
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
        )
    return views


def run(recipe: str, *, root: str | Path | None = None) -> RunResult:
    """Run one recipe's steps in order and record what it produced."""
    project = find_root(root)
    collected: list[str] = []

    # Before anything on disk changes: rules 1 to 8.
    name = resolve_name(project, recipe)
    loaded = load_recipe(project, name)
    collected.extend(loaded.warnings)
    data = load_datapackage(project)
    _, raw_described = check_all(project, loaded, data, collected)

    # Rule 9: a leftover file can never pass for a fresh result.
    _remove_outputs(project, loaded.outputs)
    try:
        _empty_work(project, name)
        (project / VIEWS).mkdir(exist_ok=True)

        # Rules 10 and 11.
        for step in loaded.steps:
            code, stderr = run_step(project, step)
            if code != 0:
                raise _step_failure(step, code, stderr)

        # Rule 12.
        views = _check_outputs(project, name, loaded)

        # Rule 13: rebuild from records; only this recipe's files were described.
        old_views, old_raws = split_resources(data)
        records = [r for r in data["recipes"] if r["name"] != name] + [loaded.record()]
        all_views = {p: r for p, r in old_views.items() if r["producedBy"].get("recipe") != name}
        all_views.update(views)
        all_raws = {**old_raws, **raw_described}
        write_generated(project, assemble(project, records, all_views, all_raws))
    except BaseException:
        # On any failure after rule 9, a failed run leaves its views absent.
        try:
            _remove_outputs(project, loaded.outputs)
        except ProjectError:
            pass
        raise
    return RunResult(recipe=name, outputs=loaded.outputs, warnings=tuple(collected))

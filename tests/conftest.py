"""Shared fixtures.

Scenario tests take the `driver` fixture, which runs each scenario twice:
once through the Python API and once through the command line
(`python -m useful_views`), both from inside the project folder.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

import useful_views as uvs

DATA = Path(__file__).resolve().parent / "data"
# The PWT example exactly as useful-views-data publishes it, plus the outputs
# it must produce (datapackage.json, useful-views/).
REFERENCE = DATA / "examples" / "pwt"


@dataclass
class Outcome:
    """What an operation did, in a form both drivers can fill."""

    ok: bool
    value: object = None  # API return value (None for the CLI)
    error: Exception | None = None  # API exception (None for the CLI)
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    kinds: list[str] = field(default_factory=list)  # check() finding kinds
    warnings: list[str] = field(default_factory=list)

    @property
    def message(self) -> str:
        return str(self.error) if self.error is not None else self.stderr

    @property
    def rule(self) -> int | None:
        return getattr(self.error, "rule", None)


class ApiDriver:
    name = "api"

    def __init__(self, cwd: Path):
        self.cwd = cwd

    def _call(self, fn, *args) -> Outcome:
        old = Path.cwd()
        os.chdir(self.cwd)
        import warnings

        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", uvs.UsefulViewsWarning)
                try:
                    value = fn(*args)
                except uvs.UsefulViewsError as exc:
                    return Outcome(ok=False, error=exc, exit_code=3,
                                   warnings=[str(w.message) for w in caught])
            out = Outcome(ok=True, value=value, warnings=[str(w.message) for w in caught])
            if fn is uvs.check:
                out.kinds = [f.kind for f in value]
                out.ok = True
                out.exit_code = 1 if value else 0
            return out
        finally:
            os.chdir(old)

    def init(self, path: str = ".") -> Outcome:
        return self._call(uvs.init, path)

    def new_recipe(self, name: str) -> Outcome:
        return self._call(uvs.new_recipe, name)

    def run(self, recipe: str) -> Outcome:
        return self._call(uvs.run, recipe)

    def check(self) -> Outcome:
        return self._call(uvs.check)

    def sync(self) -> Outcome:
        return self._call(uvs.sync)


class CliDriver:
    name = "cli"

    def __init__(self, cwd: Path):
        self.cwd = cwd

    def _call(self, *args: str) -> Outcome:
        proc = subprocess.run(
            [sys.executable, "-m", "useful_views", *args],
            cwd=self.cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        warnings_ = [line[len("warning: "):] for line in proc.stderr.splitlines() if line.startswith("warning: ")]
        out = Outcome(ok=proc.returncode == 0, exit_code=proc.returncode, stdout=proc.stdout,
                      stderr=proc.stderr, warnings=warnings_)
        if args and args[0] == "check":
            out.ok = proc.returncode in (0, 1)
            if proc.returncode == 1:
                out.kinds = [line.split(":", 1)[0] for line in proc.stdout.splitlines() if ":" in line]
        return out

    def init(self, path: str = ".") -> Outcome:
        return self._call("init", path)

    def new_recipe(self, name: str) -> Outcome:
        return self._call("new-recipe", name)

    def run(self, recipe: str) -> Outcome:
        return self._call("run", recipe)

    def check(self) -> Outcome:
        return self._call("check")

    def sync(self) -> Outcome:
        return self._call("sync")


@pytest.fixture(autouse=True)
def offline_examples(monkeypatch):
    """example() reads tests/data instead of GitHub, so no test needs the network."""
    from useful_views.examples import DATA_URL_ENV

    monkeypatch.setenv(DATA_URL_ENV, DATA.as_uri())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An initialised, empty project."""
    return uvs.init(tmp_path / "proj")


@pytest.fixture(params=["api", "cli"])
def driver(request, project: Path):
    """Runs operations from inside `project`, through the API or the CLI."""
    cls = ApiDriver if request.param == "api" else CliDriver
    return cls(project)


@pytest.fixture
def reference_project(tmp_path: Path) -> Path:
    """A project named 'reference' holding the reference raw file and recipes,
    not yet run."""
    root = uvs.init(tmp_path / "reference")
    shutil.copy(REFERENCE / "raw" / "pwt110_bra_deu_esp_usa.csv", root / "raw")
    shutil.copytree(REFERENCE / "recipes", root / "recipes", dirs_exist_ok=True)
    return root


# --- helpers for building small projects -----------------------------------


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8", newline="\n")
    return path


def toml_list(paths) -> str:
    return "[" + ", ".join(f'"{p}"' for p in paths) + "]"


def write_recipe(root: Path, name: str, *, inputs, outputs, steps: dict[str, str] | None = None,
                 description: str | None = None) -> Path:
    """Write recipes/<name>/ with a recipe.toml and step files. By default it
    has one step that writes a small parquet table to every output."""
    description = description or f"Test recipe {name}."
    write(root, f"recipes/{name}/recipe.toml",
          f'description = "{description}"\ninputs = {toml_list(inputs)}\noutputs = {toml_list(outputs)}\n')
    if steps is None:
        steps = {"01_make.py": make_views_step(outputs)}
    for filename, code in steps.items():
        write(root, f"recipes/{name}/{filename}", code)
    return root / "recipes" / name


def make_views_step(outputs, description: str = "Write a small table to each output.") -> str:
    """Code for a step that writes a two-column parquet table to each output."""
    return (
        f"# {description}\n"
        "import pyarrow as pa, pyarrow.parquet as pq\n"
        "table = pa.table({'id': pa.array([1, 2, 3], pa.int64()), 'label': ['a', 'b', 'c']})\n"
        + "".join(f"pq.write_table(table, {o!r})\n" for o in outputs)
    )


def add_raw_csv(root: Path, rel: str = "raw/data.csv", text: str = "id,value\n1,2.5\n2,3.5\n") -> str:
    write(root, rel, text)
    return rel


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under root, as relative path -> bytes, plus empty folders."""
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        if not dirnames and not filenames:
            snap[rel_dir + "/"] = b""
        for f in filenames:
            p = Path(dirpath) / f
            snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def generated(root: Path) -> tuple[bytes, bytes]:
    return (root / "datapackage.json").read_bytes(), (root / "useful-views" / "README.md").read_bytes()

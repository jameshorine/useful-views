"""Finding the project root, and creating a project with init()."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .errors import ProjectError, ProjectNotFound, warn
from .paths import CATALOG, DATAPACKAGE, RAW, RECIPES, VIEWS, WORK
from .record import empty_datapackage, load_datapackage, render_catalog, render_datapackage, write_atomic

GITIGNORE_LINES = ("raw/*", "!raw/.gitkeep", "work/", f"{VIEWS}/*.parquet")


def template(name: str) -> str:
    return resources.files("useful_views").joinpath("templates", name).read_text(encoding="utf-8")


def _project_marker(folder: Path) -> bool | None:
    """True if `folder` holds a useful-views datapackage.json, False if it holds
    some other datapackage.json or none, None if ours looks damaged."""
    path = folder / DATAPACKAGE
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None if (folder / RECIPES).is_dir() else False
    return isinstance(data, dict) and "recipes" in data


def find_root(root: str | Path | None = None) -> Path:
    """The project root: `root` if given, otherwise the nearest folder at or
    above the current directory holding a useful-views datapackage.json."""
    if root is not None:
        folder = Path(root).resolve()
        marker = _project_marker(folder)
        if marker is None:
            load_datapackage(folder)  # raises a ProjectError explaining the damage
        if not marker:
            raise ProjectNotFound(
                f"{folder} is not a useful-views project: it has no datapackage.json written by "
                "useful-views. Pass the folder that holds it, or create a project with useful-views init."
            )
        return folder
    start = Path.cwd().resolve()
    for folder in (start, *start.parents):
        marker = _project_marker(folder)
        if marker is None:
            load_datapackage(folder)
        if marker:
            return folder
    raise ProjectNotFound(
        f"No useful-views project found in {start} or any folder above it. Run this from inside a "
        "project (a folder with datapackage.json), or create one with useful-views init."
    )


def root() -> Path:
    """Return the project root, so a notebook can build paths to views."""
    return find_root()


def init(path: str | Path = ".") -> Path:
    """Create the project structure at `path`. Safe to run twice; never
    overwrites a file. Returns the project root."""
    folder = Path(path).resolve()
    for ancestor in folder.parents:
        marker = _project_marker(ancestor)
        if marker is None:
            load_datapackage(ancestor)  # raises a ProjectError explaining how to restore it
        if marker:
            raise ProjectError(
                f"{folder} is inside the useful-views project at {ancestor}. Projects cannot be nested; "
                f"run init in a folder outside {ancestor}."
            )
    if folder.exists() and not folder.is_dir():
        raise ProjectError(f"{folder} is a file, not a folder. Choose another path for the project.")
    if (folder / DATAPACKAGE).is_file() and _project_marker(folder) is False:
        raise ProjectError(
            f"{folder / DATAPACKAGE} exists but was not written by useful-views. Move it elsewhere "
            "(raw/ is a good place for a data portal's own datapackage.json), then run init again."
        )

    for sub in (RAW, RECIPES, WORK, VIEWS):
        (folder / sub).mkdir(parents=True, exist_ok=True)

    def create(rel: str, text: str) -> None:
        target = folder / rel
        if not target.exists():
            write_atomic(target, text)

    create(f"{RAW}/.gitkeep", "")
    create("README.md", template("project_README.md").replace("{project}", folder.name))

    gitignore = folder / ".gitignore"
    if gitignore.exists():
        present = {line.strip() for line in gitignore.read_text(encoding="utf-8-sig", errors="replace").splitlines()}
        missing = [line for line in GITIGNORE_LINES if line not in present]
        if missing:
            warn(
                ".gitignore already exists, so useful-views left it alone, but it lacks these lines: "
                + ", ".join(missing)
                + ". Add them so raw data, work files and views are not committed."
            )
    else:
        write_atomic(gitignore, "\n".join(GITIGNORE_LINES) + "\n")

    if not (folder / DATAPACKAGE).exists():
        write_atomic(folder / DATAPACKAGE, render_datapackage(empty_datapackage(folder)))
    if not (folder / CATALOG).exists():
        write_atomic(folder / CATALOG, render_catalog(load_datapackage(folder)))
    return folder

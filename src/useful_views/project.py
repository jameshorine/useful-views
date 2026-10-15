"""Finding the project root, and creating a project with init()."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

from .errors import ProjectError, ProjectNotFound, warn
from .paths import CATALOG, DATAPACKAGE, RAW, RECIPES, VIEWS, WORK
from .record import empty_datapackage, load_datapackage, render_catalog, render_datapackage, write_atomic

GITIGNORE_LINES = ("raw/*", "!raw/.gitkeep", f"/{WORK}/", f"{VIEWS}/*.parquet", "__pycache__/")

# What must stay out of git, a path to test it with, and the lines that do it.
KEEP_OUT = (
    # Probes without an extension, so a rule such as *.csv does not pass for
    # every raw file.
    (f"{RAW}/useful-views-probe", ("raw/*",), {"raw/*", "raw/**", "raw"}),
    (f"{WORK}/useful-views-probe/probe", (f"/{WORK}/",), {"work", "work/*", "work/**", "**/work"}),
    (f"{VIEWS}/useful-views-probe.parquet", (f"{VIEWS}/*.parquet",),
     {"useful-views/*.parquet", "useful-views/*", "useful-views/**", "useful-views", "*.parquet", "**/*.parquet"}),
)


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
        folder = Path(root).expanduser().resolve()
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
    """The project folder, found from the current folder.

    Walks up from the current folder to the nearest `datapackage.json` that
    useful-views wrote, so it works from a notebook in `notebooks/`. Use it
    to build paths to views and read them with any library:

        gdp = pl.read_parquet(uvs.root() / "useful-views" / "gdp-per-capita.parquet")

    Returns:
        The project folder, as an absolute path.

    Raises:
        ProjectNotFound: The current folder is not inside a project.
    """
    return find_root()


def check_not_nested(folder: Path) -> None:
    """Refuse to create a project inside another one."""
    for ancestor in folder.parents:
        marker = _project_marker(ancestor)
        if marker is None:
            load_datapackage(ancestor)  # raises a ProjectError explaining how to restore it
        if marker:
            raise ProjectError(
                f"{folder} is inside the useful-views project at {ancestor}. Projects cannot be nested; "
                f"choose a folder outside {ancestor}."
            )


def _not_ignored(folder: Path, gitignore: Path) -> list[str]:
    """The .gitignore lines to add so raw data, work files and views stay out
    of git. Inside a git repository, git itself is asked (so other .gitignore
    files and global excludes count); otherwise the lines are compared, with
    equivalent forms such as /work/ and work accepted."""
    if shutil.which("git"):
        probe = [rel for rel, _, _ in KEEP_OUT]
        try:
            inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=folder,
                                    capture_output=True, text=True, timeout=30)
            if inside.returncode == 0 and inside.stdout.strip() == "true":
                found = subprocess.run(["git", "check-ignore", "--no-index", "-v", *probe], cwd=folder,
                                       capture_output=True, text=True, timeout=30)
                if found.returncode in (0, 1):
                    # Count only rules in a .gitignore, which teammates get too, not
                    # this computer's own .git/info/exclude or global excludes.
                    ignored = set()
                    for line in found.stdout.splitlines():
                        source, _, path = line.partition("\t")
                        if source.split(":", 1)[0].replace("\\", "/").rsplit("/", 1)[-1] == ".gitignore":
                            ignored.add(path)
                    return [line for rel, lines, _ in KEEP_OUT if rel not in ignored for line in lines]
        except (OSError, subprocess.SubprocessError):
            pass
    text = gitignore.read_text(encoding="utf-8-sig", errors="replace")
    present = {line.strip().strip("/") for line in text.splitlines() if line.strip() and not line.startswith("#")}
    return [line for _, lines, accepted in KEEP_OUT if not present & accepted for line in lines]


def too_broad(folder: Path) -> str | None:
    """Why a project here would be a mistake, or None."""
    if folder.parent == folder:
        return "is the top of a drive"
    try:
        home = Path.home().resolve()
        if folder == home or (folder.exists() and os.path.samefile(folder, home)):
            return "is your home folder"
    except (RuntimeError, OSError):
        pass  # no home folder known
    return None


def init(path: str | Path = ".", *, force: bool = False) -> Path:
    """Create the project structure at `path`.

    Creates `raw/`, `recipes/`, `work/` and `useful-views/`, a starter
    `README.md`, an `AGENTS.md` with instructions for AI coding assistants, a
    `.gitignore` that keeps raw data, work files and views out of git, and an
    empty `datapackage.json` and catalog. Safe to run twice: it never
    overwrites a file. If a `.gitignore` is already there, it warns when raw
    data, work files or views would not be ignored (asking git, inside a git
    repository), naming the lines to add.

    Args:
        path: The project folder. Created, with its parents, if missing.
        force: Create the project even in your home folder or at the top of a
            drive, which init otherwise refuses: every folder below would then
            be inside the project.

    Returns:
        The project folder, as an absolute path.

    Raises:
        ProjectError: `path` is inside another project, is a file, holds a
            `datapackage.json` that useful-views did not write, or is your home
            folder or the top of a drive (without `force`).
    """
    folder = Path(path).expanduser().resolve()
    why = too_broad(folder)
    if why and not force:
        raise ProjectError(
            f"{folder} {why}. A project there would take in every folder below it, and no other project could "
            "be made inside it. Make one in its own folder instead, such as: useful-views init my-project "
            "(use --force, or force=True, if you really mean this folder)."
        )
    check_not_nested(folder)
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
    create("AGENTS.md", template("AGENTS.md"))

    gitignore = folder / ".gitignore"
    if gitignore.exists():
        missing = _not_ignored(folder, gitignore)
        if missing:
            warn(
                ".gitignore already exists, so useful-views left it alone, but git would not ignore raw data, "
                "work files or views. Add these lines to it: " + ", ".join(missing)
                + ". Otherwise git add would commit data that may be large, private or licensed."
            )
    else:
        write_atomic(gitignore, "\n".join(GITIGNORE_LINES) + "\n")

    if not (folder / DATAPACKAGE).exists():
        write_atomic(folder / DATAPACKAGE, render_datapackage(empty_datapackage(folder)))
    if not (folder / CATALOG).exists():
        write_atomic(folder / CATALOG, render_catalog(load_datapackage(folder)))
    return folder

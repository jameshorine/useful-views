"""Project-relative path helpers shared by the rest of the package."""

from __future__ import annotations

import os
import posixpath
import re
import stat
from pathlib import Path

from .errors import UsefulViewsError

DATAPACKAGE = "datapackage.json"
RAW = "raw"
RECIPES = "recipes"
WORK = "work"
VIEWS = "useful-views"
CATALOG = f"{VIEWS}/README.md"

# Extensions a view may have. Add to this tuple to allow more formats.
VIEW_EXTENSIONS = (".parquet",)

IGNORED_NAMES = frozenset({"__pycache__", "Thumbs.db", "desktop.ini"})


class UnreadableFolder(UsefulViewsError):
    """A folder the package needs to look inside cannot be read."""

    def __init__(self, folder: Path):
        super().__init__(
            f"Cannot read the folder {folder}: permission denied. Fix its permissions so you can open it."
        )
        self.folder = folder


def is_file(path: Path) -> bool:
    """Like Path.is_file(), but a permission problem raises PermissionError
    on every Python version (3.14's Path.is_file() returns False instead)."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def is_ignored(name: str) -> bool:
    """Hidden files and tool clutter are ignored everywhere the package scans."""
    return name.startswith(".") or name in IGNORED_NAMES


def listdir(folder: Path) -> list[str]:
    """Sorted names in `folder`, without ignored ones. Empty if it is missing."""
    try:
        names = os.listdir(folder)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except PermissionError as exc:
        raise UnreadableFolder(folder) from exc
    return sorted(n for n in names if not is_ignored(n))


def exists_exact(root: Path, rel: str) -> bool:
    """True if `rel` exists under `root` with exactly this letter case.

    On macOS and Windows, `Path.exists()` ignores case, so a recipe could pass
    there and fail on a Linux clone. This walks each component instead.
    """
    current = root
    for part in rel.split("/"):
        try:
            names = os.listdir(current)
        except (FileNotFoundError, NotADirectoryError):
            return False
        except PermissionError as exc:
            raise UnreadableFolder(current) from exc
        if part not in names:
            return False
        current = current / part
    return current.exists()  # a broken symlink counts as missing


WINDOWS_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f]')
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE)


def q(text: str) -> str:
    """Quote a path for a message without doubling its backslashes."""
    return f"'{text}'"


DRIVE = re.compile(r"^[A-Za-z]:")
CONTROL = re.compile(r"[\x00-\x1f]")


def canonical(rel: str, root: Path | None = None) -> str | None:
    """`rel` rewritten in the one accepted form, or None if it leaves the project."""
    p = rel.strip().replace("\\", "/")
    if DRIVE.match(p) and os.name != "nt":
        return None  # a Windows path cannot point inside a project on this computer
    if p.startswith("~"):
        p = os.path.expanduser(p)
        if p.startswith("~"):
            return None
    if p.startswith("/") or DRIVE.match(p):
        if root is None:
            return None
        try:
            p = Path(p).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
    p = posixpath.normpath(p)
    if p in (".", "..", "") or p.startswith(("../", "/", "~")) or DRIVE.match(p):
        return None
    return p


def path_problem(rel: object, root: Path | None = None, example: str = "raw/data.csv") -> str | None:
    """Describe what is wrong with how a recipe.toml path is written, or None.

    Paths are compared as plain strings, so they must be written one way only:
    relative to the project root, with single forward slashes. The message
    gives the corrected string where there is one.
    """
    if not isinstance(rel, str):
        return f"{rel!r} is not a string; write it in double quotes, like {q(example)}"
    if rel.strip() == "":
        return f"an empty string is not a path; write one like {q(example)}"
    if CONTROL.search(rel):
        # In TOML double quotes, a backslash starts an escape such as \n or \t.
        return (
            f"{rel!r} contains a control character, usually because a backslash was read as an escape "
            "(in recipe.toml, \\n means a new line). Write paths with forward slashes, "
            f"like {q(example)}"
        )
    if rel.endswith(("/", "\\")):
        return f"{q(rel)} ends with a slash, but a path must name a file, like {q(example)}"
    if rel != rel.strip():
        reason = "has spaces around it"
    elif "\\" in rel:
        reason = "uses backslashes; paths use forward slashes"
    elif rel.startswith(("~", "/")) or DRIVE.match(rel):
        reason = "is not relative to the project root"
    elif rel.startswith("./"):
        reason = "starts with './'"
    elif "//" in rel:
        reason = "has a double slash"
    elif any(part in (".", "..") for part in rel.split("/")):
        reason = "contains '.' or '..'"
    else:
        reason = None
    if reason:
        fixed = canonical(rel, root)
        if fixed is None:
            return (
                f"{q(rel)} {reason}, and points outside the project. Move the file into the project and "
                f"write its path from the project root, like {q(example)}"
            )
        return f"{q(rel)} {reason}; write {q(fixed)}"
    for part in rel.split("/"):
        if WINDOWS_BAD_CHARS.search(part) or part.endswith((".", " ")) or WINDOWS_RESERVED.match(part):
            return (
                f"{q(rel)} contains a name ({q(part)}) that cannot exist on Windows. Rename it using letters, "
                "digits, '-', '_' and '.', not ending in '.' or a space"
            )
    return None


def resource_name(rel: str) -> str:
    """A resource's name: its file name without the extension, made safe for
    the Data Package standard (lowercase a-z, 0-9, '-', '_', '.')."""
    stem = rel.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return slug(stem)


def slug(text: str) -> str:
    """Lowercase, with anything outside ASCII a-z, 0-9, '-', '_', '.' replaced by '-'."""
    return re.sub(r"[^a-z0-9._-]", "-", text.lower()) or "-"

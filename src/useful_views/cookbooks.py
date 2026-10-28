"""cookbook(): write cookbook.json, the ledger of the recipes a project uses.

Like `uv lock` writing `uv.lock`, the cookbook is produced by a command and
never edited by hand. Version 1 records each recipe and its steps, in run
order, and the raw inputs they read. Later versions are planned to add the
SHA-256 of every file and the environment the steps ran in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ProjectError, UsefulViewsError
from .paths import RAW
from .project import find_root
from .lock import project_lock
from .record import load_datapackage, run_order, write_atomic

COOKBOOK = "cookbook.json"
FORMAT_VERSION = 1

# check() findings that do not stop a cookbook from being written.
NOT_BLOCKING = {"stray-file", "stale-cookbook", "stale-catalog"}

# With check=True, only findings that mean datapackage.json no longer matches
# the recipe folders count; they need no data on disk, so a CI job with only
# the git checkout (no raw/, no views) gets a real answer.
RECORD_KINDS = {"unreadable-recipe", "refused", "never-run", "missing-recipe-folder"}
RECORD_REASONS = {"changed", "outputs-changed", "outputs-dropped"}


def _out_of_record(finding) -> bool:
    return finding.kind in RECORD_KINDS or (finding.kind == "out-of-date" and finding.reason in RECORD_REASONS)


class CookbookError(UsefulViewsError):
    """The cookbook cannot be written because the project is not in sync.

    Attributes:
        findings: The `check` findings that must be resolved first.
    """

    def __init__(self, message: str, *, findings: list | None = None):
        super().__init__(message)
        self.findings = list(findings or [])

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["findings"] = [f.to_dict() for f in self.findings]
        return data


@dataclass(frozen=True)
class CookbookResult:
    """What `cookbook` did, or found with `check=True`.

    Attributes:
        path: The cookbook file, `cookbook.json` at the project root.
        written: True if the file was created or changed.
        up_to_date: True if the file on disk matches the project's recipes
            (after writing, always True; with `check=True`, the answer).
        recipes: The recipes in the cookbook, in run order.
        findings: With `check=True`, the `check` findings that mean the
            recorded recipes no longer match the recipe folders (so the
            cookbook cannot be up to date); otherwise empty.
    """

    path: Path
    written: bool
    up_to_date: bool
    recipes: tuple[str, ...] = field(default=())
    findings: tuple = field(default=())

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "written": self.written,
            "up_to_date": self.up_to_date,
            "recipes": list(self.recipes),
            "findings": [f.to_dict() for f in self.findings],
        }


def build(data: dict) -> dict:
    """The cookbook for a project's recorded recipes (datapackage.json)."""
    records = {r["name"]: r for r in data["recipes"]}
    order = run_order(records)
    raw = sorted({p for r in records.values() for p in r["inputs"] if p.startswith(f"{RAW}/")})
    return {
        "version": FORMAT_VERSION,
        "generator": "useful-views",
        "project": data.get("name"),
        "raw_inputs": raw,
        "recipes": [records[name] for name in order],
    }


def render(book: dict) -> str:
    return json.dumps(book, indent=2, ensure_ascii=False) + "\n"


def expected_text(project: Path) -> str:
    """What cookbook.json should contain for the project's current records."""
    return render(build(load_datapackage(project)))


def read_cookbook(project: Path) -> str | None:
    """The text of cookbook.json with LF line endings, or None if there is none.

    A file that is not UTF-8 text reads as "", which never matches, so it
    counts as out of date and `cookbook` rewrites it. A file that cannot be
    read at all raises ProjectError.
    """
    path = project / COOKBOOK
    if path.is_dir():  # tested first: Windows reports a folder as "permission denied"
        raise ProjectError(f"{COOKBOOK} is a folder. Delete it, then run useful-views cookbook.")
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProjectError(
            f"Could not read {COOKBOOK}: {exc.strerror or exc}. Check its permissions, or delete it and run "
            "useful-views cookbook."
        ) from exc
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return ""


def stale_warning(project: Path) -> str | None:
    """A warning to give after records changed, if there is a cookbook and it
    no longer matches them."""
    try:
        current = read_cookbook(project)
        if current is None or current == expected_text(project):
            return None
    except UsefulViewsError:
        return None  # check reports a cookbook that cannot be read
    return f"{COOKBOOK} no longer matches the recorded recipes. Update it with: useful-views cookbook"


def cookbook(*, root: str | Path | None = None, check: bool = False) -> CookbookResult:
    """Write `cookbook.json`, the ledger of the recipes the project uses.

    The cookbook lists every recorded recipe, in run order (upstream first),
    with its description, inputs, outputs and steps, and the raw inputs the
    recipes read. It is generated from the record in `datapackage.json`, so
    to write it the project must be in sync: if `check` finds anything other
    than stray files or a stale catalog or cookbook, nothing is written. Writing an unchanged cookbook
    leaves the file untouched. Commit it to git, like a lock file.

    `check=True` needs no data: it works on a fresh clone or in CI, where
    raw/ and the views are not present. It compares `cookbook.json` with the
    committed `datapackage.json`, and also reports recipes whose folder no
    longer matches their record (edited, added or removed without a sync).

    Args:
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.
        check: Do not write; report whether `cookbook.json` matches the
            project's recipes. Use it in CI.

    Returns:
        A `CookbookResult`. With `check=True`, its `up_to_date` is the
        answer, and its `findings` list recipes whose record is out of date.

    Raises:
        CookbookError: When writing, the project is not in sync; its
            `findings` say why.
        ProjectError: cookbook.json exists but cannot be read.
        ProjectNotFound: No project was found.
    """
    from .status import check as check_project, syncable

    project = find_root(root)
    with project_lock(project, enabled=not check):
        findings = check_project(root=project)
        data = load_datapackage(project)
        book = build(data)
        text = render(book)
        path = project / COOKBOOK
        current = read_cookbook(project)
        recipes = tuple(r["name"] for r in book["recipes"])
        if check:
            stale = tuple(f for f in findings if _out_of_record(f))
            return CookbookResult(path=path, written=False, up_to_date=current == text and not stale,
                                  recipes=recipes, findings=stale)
        blocking = [f for f in findings if f.kind not in NOT_BLOCKING]
        if blocking:
            listing = "\n".join(f"  - {f.kind}: {f.message.splitlines()[0]}" for f in blocking)
            if all(syncable(f) for f in blocking):
                advice = "Run useful-views sync, then try again."
            else:
                advice = ("Fix what is listed (useful-views check shows each in full), run useful-views sync, "
                          "then try again.")
            raise CookbookError(
                "The cookbook describes a project in sync, but this one is not:\n" + listing + "\n" + advice,
                findings=blocking,
            )
        if current != text:
            write_atomic(path, text)
        return CookbookResult(path=path, written=current != text, up_to_date=True, recipes=recipes)

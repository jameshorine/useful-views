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

from .errors import UsefulViewsError
from .paths import RAW
from .project import find_root
from .record import load_datapackage, write_atomic

COOKBOOK = "cookbook.json"
FORMAT_VERSION = 1

# check() findings that do not stop a cookbook from being written.
NOT_BLOCKING = {"stray-file", "stale-cookbook"}


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
    """

    path: Path
    written: bool
    up_to_date: bool
    recipes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "written": self.written,
            "up_to_date": self.up_to_date,
            "recipes": list(self.recipes),
        }


def _run_order(records: dict[str, dict]) -> list[str]:
    """Recipe names, upstream first; recipes that do not depend on each other
    in name order."""
    makers = {out: name for name, r in records.items() for out in r["outputs"]}
    upstream = {
        name: sorted({makers[i] for i in r["inputs"] if i in makers} - {name}) for name, r in records.items()
    }
    order: list[str] = []
    done: set[str] = set()

    def visit(name: str, seen: tuple[str, ...] = ()) -> None:
        if name in done or name in seen:
            return
        for up in upstream[name]:
            visit(up, (*seen, name))
        done.add(name)
        order.append(name)

    for name in sorted(records):
        visit(name)
    return order


def build(data: dict) -> dict:
    """The cookbook for a project's recorded recipes (datapackage.json)."""
    records = {r["name"]: r for r in data["recipes"]}
    order = _run_order(records)
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


def cookbook(*, root: str | Path | None = None, check: bool = False) -> CookbookResult:
    """Write `cookbook.json`, the ledger of the recipes the project uses.

    The cookbook lists every recorded recipe, in run order (upstream first),
    with its description, inputs, outputs and steps, and the raw inputs the
    recipes read. It is generated from the record in `datapackage.json`, so
    the project must be in sync first: if `check` finds anything other than
    stray files, nothing is written. Writing an unchanged cookbook leaves the
    file untouched. Commit it to git, like a lock file.

    Args:
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.
        check: Do not write; report whether `cookbook.json` matches the
            project. Use it in CI.

    Returns:
        A `CookbookResult`. With `check=True`, its `up_to_date` is the answer.

    Raises:
        CookbookError: The project is not in sync; its `findings` say why.
            Run `useful-views sync`, then try again.
        ProjectNotFound: No project was found.
    """
    from .status import check as check_project

    project = find_root(root)
    blocking = [f for f in check_project(root=project) if f.kind not in NOT_BLOCKING]
    if blocking:
        listing = "\n".join(f"  - {f.kind}: {f.message.splitlines()[0]}" for f in blocking)
        raise CookbookError(
            "The cookbook describes a project in sync, but this one is not:\n"
            + listing
            + "\nRun useful-views sync (and fix anything it refuses), then try again.",
            findings=blocking,
        )
    data = load_datapackage(project)
    book = build(data)
    text = render(book)
    path = project / COOKBOOK
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    recipes = tuple(r["name"] for r in book["recipes"])
    if check:
        return CookbookResult(path=path, written=False, up_to_date=current == text, recipes=recipes)
    if current != text:
        write_atomic(path, text)
    return CookbookResult(path=path, written=current != text, up_to_date=True, recipes=recipes)

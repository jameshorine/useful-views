"""sync(): make the views and their records match the recipe folders.

Like `uv sync`: what is no longer declared is removed, and what is missing
or out of date is made, by actually running recipes in dependency order.
Records only ever come from real runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ProjectError, RecipeRefused, UsefulViewsError, warn
from .paths import RECIPES, VIEWS, exists_exact, is_file, listdir
from .project import find_root
from .recipe import Recipe, load_recipe, resolve_name
from .record import assemble, load_datapackage, retry, split_resources, write_generated
from .rules import self_read
from .runner import _write, run


@dataclass(frozen=True)
class SyncResult:
    removed: tuple[str, ...]  # recipes whose records were removed (their folder is gone)
    dropped: tuple[str, ...]  # views removed from records because their recipe no longer declares them
    deleted: tuple[str, ...]  # view files deleted from useful-views/
    ran: tuple[str, ...]  # recipes run, in the order they ran
    warnings: tuple[str, ...]


def _load_all(project: Path, collected: list[str]) -> dict[str, Recipe]:
    """Every recipe folder, checked (rules 1 to 3). A folder without a
    recipe.toml is skipped with a warning; any other problem is refused."""
    loaded = {}
    for name in listdir(project / RECIPES):
        if not (project / RECIPES / name).is_dir():
            continue
        try:
            has_toml = is_file(project / RECIPES / name / "recipe.toml")
        except PermissionError as exc:
            raise RecipeRefused(
                f"Cannot read {RECIPES}/{name}/: permission denied. Fix the folder's permissions, then run "
                "useful-views sync again.",
                rule=1,
                path=f"{RECIPES}/{name}/recipe.toml",
            ) from exc
        if not has_toml:
            warn(f"{RECIPES}/{name}/ has no recipe.toml, so sync leaves it alone.", collected)
            continue
        resolve_name(project, name)  # refuses a folder name that is not allowed
        recipe = load_recipe(project, name)
        collected.extend(recipe.warnings)
        loaded[name] = recipe
    return loaded


def _run_order(loaded: dict[str, Recipe], wanted: set[str], recorded: set[str]) -> list[str]:
    """`wanted` plus everything downstream of it, upstream first. Among
    recipes that do not depend on each other, ones with a record run first,
    so their old records are refreshed before new recipes are checked
    against them."""
    for name, r in loaded.items():
        own = [i for i in r.inputs if self_read(r, i)]
        if own:
            raise RecipeRefused(
                f"{RECIPES}/{name}/recipe.toml lists {own[0]!r} as both an input and an output. A recipe "
                "cannot read its own view; use a work file in work/ for intermediate data.",
                rule=6,
                path=f"{RECIPES}/{name}/recipe.toml",
            )
    makers = {out: name for name, r in loaded.items() for out in r.outputs}
    upstream = {name: sorted({makers[i] for i in r.inputs if i in makers} - {name}) for name, r in loaded.items()}
    downstream: dict[str, set[str]] = {name: set() for name in loaded}
    for name, ups in upstream.items():
        for up in ups:
            downstream[up].add(name)

    todo = set(wanted)
    stack = list(wanted)
    while stack:
        for child in downstream[stack.pop()]:
            if child not in todo:
                todo.add(child)
                stack.append(child)

    order: list[str] = []
    done: set[str] = set()
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in visiting:
            cycle = " -> ".join([*visiting[visiting.index(name):], name])
            raise RecipeRefused(
                f"The recipes form a cycle: {cycle} (each reads a view made by the next). "
                "Remove one of those inputs from its recipe.toml.",
                rule=6,
                path=f"{RECIPES}/{name}/recipe.toml",
            )
        visiting.append(name)
        for up in upstream[name]:
            visit(up)
        visiting.pop()
        done.add(name)
        if name in todo:
            order.append(name)

    for name in sorted(loaded, key=lambda n: (n not in recorded, n)):
        visit(name)
    return order


def sync(*, root: str | Path | None = None, all: bool = False) -> SyncResult:
    """Make the views and datapackage.json match the recipe folders.

    Removes the records and view files of recipes whose folder is gone and of
    outputs a recipe no longer declares. Then runs, upstream first, every
    recipe that has never run, whose recipe folder no longer matches its
    record, or whose views are missing, and everything downstream of those.
    With all=True it runs every recipe. Stops at the first failing recipe;
    the ones before it stay recorded.
    """
    rerun_all = all
    project = find_root(root)
    collected: list[str] = []

    # Plan, before anything on disk changes.
    data = load_datapackage(project)
    loaded = _load_all(project, collected)
    folders = {n for n in listdir(project / RECIPES) if (project / RECIPES / n).is_dir()}
    declared_outputs = {out for r in loaded.values() for out in r.outputs}

    kept, removed, dropped = [], [], []
    for record in data["recipes"]:
        name = record["name"]
        if name not in folders:
            removed.append(record)
            continue
        if name in loaded:
            stale = [o for o in record["outputs"] if o not in loaded[name].outputs]
            if stale:
                dropped.extend(stale)
                record = {**record, "outputs": [o for o in record["outputs"] if o not in stale]}
                if not record["outputs"]:
                    continue  # no run produced a record without views; it runs again below
        kept.append(record)

    gone = [o for r in removed for o in r["outputs"]] + dropped
    to_delete = sorted(
        {o for o in gone if o not in declared_outputs and exists_exact(project, o) and (project / o).is_file()}
    )
    records = {r["name"]: r for r in kept}
    wanted = set(loaded) if rerun_all else {
        name
        for name, recipe in loaded.items()
        if name not in records
        or records[name] != recipe.record()
        or any(not exists_exact(project, o) for o in recipe.outputs)
    }
    order = _run_order(loaded, wanted, set(records))

    # Remove what is no longer declared: the records first, then the files,
    # so a file that cannot be deleted is never still claimed by a record.
    views, raws = split_resources(data)
    views = {p: v for p, v in views.items() if p not in gone}
    write_generated(project, assemble(project, kept, views, raws))
    locked = []
    for rel in to_delete:
        try:
            retry((project / rel).unlink, project / rel)
        except OSError as exc:
            locked.append(f"  - {rel}: {exc.strerror or exc}")
    if locked:
        raise ProjectError(
            "datapackage.json and useful-views/README.md were updated, but these files could not be deleted:\n"
            + "\n".join(locked)
            + "\nClose any program that has them open, then run useful-views sync again."
        )
    for rel in sorted(set(gone)):
        readers = sorted(n for n, r in loaded.items() if rel in r.inputs)
        if readers and rel not in declared_outputs:
            warn(
                f"{', '.join(f'{RECIPES}/{n}' for n in readers)} reads {rel}, which no recipe makes any more. "
                f"Change the inputs in its recipe.toml.",
                collected,
            )

    # Make what is missing or out of date.
    ran = []
    for name in order:
        _write("stdout", f"useful-views: running {RECIPES}/{name}\n")
        try:
            result = run(name, root=project)
        except UsefulViewsError as exc:
            done = []
            if removed or dropped:
                done.append("removed records and views that are no longer declared")
            if ran:
                done.append("ran " + ", ".join(f"{RECIPES}/{n}" for n in ran))
            if done:
                exc.args = (
                    f"{exc}\n\nBefore this, sync had already {' and '.join(done)}; those changes are kept. "
                    "Fix the problem above, then run useful-views sync again.",
                    *exc.args[1:],
                )
            raise
        collected.extend(result.warnings)
        ran.append(name)

    return SyncResult(
        removed=tuple(r["name"] for r in removed),
        dropped=tuple(dropped),
        deleted=tuple(to_delete),
        ran=tuple(ran),
        warnings=tuple(dict.fromkeys(collected)),
    )

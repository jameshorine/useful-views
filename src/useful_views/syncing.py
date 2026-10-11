"""sync(): make the views and their records match the recipe folders.

Like `uv sync`: what is no longer declared is removed, and what is missing
or out of date is made, by actually running recipes in dependency order.
Records only ever come from real runs.

The plan (what to remove, what to run, and every rule a recipe would break)
is worked out before anything changes; `check()` reports the same plan.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ProjectError, RecipeRefused, UsefulViewsError, UsefulViewsWarning, warn
from .paths import RECIPES, exists_exact, is_file, listdir
from .project import find_root
from .recipe import Recipe, declared_paths, load_recipe, read_declaration, resolve_name
from .record import assemble, load_datapackage, retry, split_resources, write_generated
from .rules import Others, check_static
from .runner import _write, run


@dataclass(frozen=True)
class SyncResult:
    """What `sync` did.

    Attributes:
        removed: Recipes whose records were removed because their folder is gone.
        dropped: Views removed from a record because the recipe no longer declares them.
        deleted: View files deleted from useful-views/.
        ran: Recipes run, in the order they ran.
        warnings: Warnings issued along the way (also shown as they happen).
    """

    removed: tuple[str, ...]
    dropped: tuple[str, ...]
    deleted: tuple[str, ...]
    ran: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "removed": list(self.removed),
            "dropped": list(self.dropped),
            "deleted": list(self.deleted),
            "ran": list(self.ran),
            "warnings": list(self.warnings),
        }


@dataclass
class Plan:
    """What a sync would do, worked out without changing anything."""

    data: dict
    loaded: dict[str, Recipe]
    removed: list[dict]
    dropped: list[str]
    kept: list[dict]
    gone: list[str]
    to_delete: list[str]
    order: list[str]
    # Why each recipe in `order` runs: "never-run", "changed", "outputs-changed",
    # "views-missing", "upstream" (something it reads is made again) or "all".
    reasons: dict[str, str] = field(default_factory=dict)
    refusals: list[RecipeRefused] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def stale(self) -> set[str]:
        return {n for n, why in self.reasons.items() if why == "changed"}


def _refusal(exc: RecipeRefused, name: str) -> RecipeRefused:
    exc.recipe = exc.recipe or name
    return exc


def _graph(loaded: dict[str, Recipe]) -> tuple[dict[str, list[str]], dict[str, set[str]]]:
    makers = {out: name for name, r in loaded.items() for out in r.outputs}
    upstream = {name: sorted({makers[i] for i in r.inputs if i in makers} - {name}) for name, r in loaded.items()}
    downstream: dict[str, set[str]] = {name: set() for name in loaded}
    for name, ups in upstream.items():
        for up in ups:
            downstream[up].add(name)
    return upstream, downstream


def _run_order(loaded: dict[str, Recipe], wanted: set[str], recorded: set[str]) -> tuple[list[str], list[RecipeRefused]]:
    """`wanted` plus everything downstream of it, upstream first. Among
    recipes that do not depend on each other, ones with a record run first,
    so their old records are refreshed before new recipes are checked
    against them. Recipes in a cycle are refused (rule 6) and left out; the
    rest are still ordered."""
    loaded = dict(loaded)
    refusals: list[RecipeRefused] = []
    while True:
        upstream, downstream = _graph(loaded)
        todo = set(wanted) & set(loaded)
        stack = list(todo)
        while stack:
            for child in downstream[stack.pop()]:
                if child not in todo:
                    todo.add(child)
                    stack.append(child)
        order: list[str] = []
        done: set[str] = set()
        visiting: list[str] = []
        cycle: list[str] = []

        def visit(name: str) -> None:
            if name in done or cycle:
                return
            if name in visiting:
                cycle.extend(visiting[visiting.index(name):])
                return
            visiting.append(name)
            for up in upstream[name]:
                visit(up)
            visiting.pop()
            done.add(name)
            if name in todo:
                order.append(name)

        for name in sorted(loaded, key=lambda n: (n not in recorded, n)):
            visit(name)
        if not cycle:
            return order, refusals
        route = " -> ".join([*cycle, cycle[0]])
        for name in cycle:
            refusals.append(RecipeRefused(
                f"The recipes form a cycle: {route} (each reads a view made by the next). "
                "Remove one of those inputs from its recipe.toml.",
                rule=6,
                path=f"{RECIPES}/{name}/recipe.toml",
                recipe=name,
            ))
            del loaded[name]


def plan_sync(project: Path, *, rerun_all: bool = False) -> Plan:
    """Work out what `sync` would do, and every refusal, without changing anything."""
    data = load_datapackage(project)
    refusals: list[RecipeRefused] = []
    notes: list[str] = []
    loaded: dict[str, Recipe] = {}
    declared: dict[str, tuple[list[str], list[str]]] = {}
    folders: set[str] = set()
    unreadable: set[str] = set()
    missing_toml: set[str] = set()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UsefulViewsWarning)
        for name in listdir(project / RECIPES):
            if not (project / RECIPES / name).is_dir():
                continue
            folders.add(name)
            try:
                has_toml = is_file(project / RECIPES / name / "recipe.toml")
            except PermissionError:
                unreadable.add(name)
                refusals.append(RecipeRefused(
                    f"Cannot read {RECIPES}/{name}/: permission denied. Fix the folder's permissions.",
                    rule=1, path=f"{RECIPES}/{name}/recipe.toml", recipe=name,
                ))
                continue
            if not has_toml:
                unreadable.add(name)
                missing_toml.add(name)
                notes.append(f"{RECIPES}/{name}/ has no recipe.toml, so it is not treated as a recipe.")
                continue
            try:
                parsed = read_declaration(project, name)
                declared[name] = (declared_paths(parsed, "inputs"), declared_paths(parsed, "outputs"))
            except RecipeRefused:
                unreadable.add(name)
            try:
                resolve_name(project, name)  # refuses a folder name that is not allowed
                loaded[name] = load_recipe(project, name)
            except RecipeRefused as exc:
                refusals.append(_refusal(exc, name))

        kept, removed, dropped = [], [], []
        replaced = set()  # recipes whose record is gone because they replaced every output
        for record in data["recipes"]:
            name = record["name"]
            if name not in folders:
                removed.append(record)
                continue
            if name in loaded:
                old = [o for o in record["outputs"] if o not in loaded[name].outputs]
                if old:
                    dropped.extend(old)
                    record = {**record, "outputs": [o for o in record["outputs"] if o not in old]}
                    if not record["outputs"]:
                        replaced.add(name)
                        continue  # no run produced a record without views; it runs again below
            kept.append(record)

        declared_outputs = {out for r in loaded.values() for out in r.outputs}
        gone = [o for r in removed for o in r["outputs"]] + dropped
        to_delete = sorted(
            {o for o in gone if o not in declared_outputs and exists_exact(project, o) and (project / o).is_file()}
        )
        records = {r["name"]: r for r in kept}
        reasons: dict[str, str] = {}
        for name, recipe in loaded.items():
            if rerun_all:
                reasons[name] = "all"
            elif name in replaced:
                reasons[name] = "outputs-changed"
            elif name not in records:
                reasons[name] = "never-run"
            elif records[name] != recipe.record():
                reasons[name] = "changed"
            elif any(not exists_exact(project, o) for o in recipe.outputs):
                reasons[name] = "views-missing"
        order, cycle_refusals = _run_order(loaded, set(reasons), set(records))
        refusals.extend(cycle_refusals)
        for name in order:
            reasons.setdefault(name, "upstream")
        reasons = {name: reasons[name] for name in order}

        # Rules 4 to 8 for each recipe that will run, against the records as
        # they will be at that moment: earlier recipes in the order already
        # re-recorded, later ones still as they are. A refused maker counts as
        # running first, so its readers are not refused for the same reason.
        refused = {r.recipe for r in refusals}
        state = dict(records)
        for i, name in enumerate(order):
            if name not in refused:
                others = Others(
                    declared={n: d for n, d in declared.items() if n != name},
                    records={n: r for n, r in state.items() if n != name},
                    folders=folders,
                    unreadable=unreadable,
                    missing_toml=missing_toml,
                )
                try:
                    check_static(project, loaded[name], others, frozenset(order[:i]) | refused)
                except RecipeRefused as exc:
                    refusals.append(_refusal(exc, name))
            state[name] = loaded[name].record()

        for rel in sorted(set(gone)):
            readers = sorted(n for n, r in loaded.items() if rel in r.inputs)
            if readers and rel not in declared_outputs:
                notes.append(
                    f"{', '.join(f'{RECIPES}/{n}' for n in readers)} reads {rel}, which no recipe makes any more. "
                    "Change the inputs in its recipe.toml."
                )

    unique: dict[tuple, RecipeRefused] = {}
    for r in refusals:
        unique.setdefault((r.recipe, r.rule, r.detail), r)
    found = [str(w.message) for w in caught if issubclass(w.category, UsefulViewsWarning)]
    return Plan(
        data=data, loaded=loaded, removed=removed, dropped=dropped, kept=kept, gone=gone,
        to_delete=to_delete, order=order, reasons=reasons, refusals=list(unique.values()),
        warnings=list(dict.fromkeys(found + notes)),
    )


def _combined(refusals: list[RecipeRefused]) -> RecipeRefused:
    """One exception for every refusal: the first, with the rest attached."""
    first = refusals[0]
    if len(refusals) == 1:
        return first
    listing = "\n\n".join(f"{i}. {r}" for i, r in enumerate(refusals, 1))
    combined = RecipeRefused(
        f"sync found {len(refusals)} problems, so it changed nothing. Fix them, then run useful-views sync "
        f"again:\n\n{listing}",
        path=first.path,
        recipe=first.recipe,
    )
    combined.rule, combined.rule_title = first.rule, first.rule_title
    combined.refusals = refusals
    return combined


def sync(*, root: str | Path | None = None, all: bool = False) -> SyncResult:
    """Make the views and datapackage.json match the recipe folders, like `uv sync`.

    First, every recipe folder is checked against the rules; if any recipe
    would be refused, sync raises before changing anything, listing every
    problem. Then it removes the records and view files of recipes whose
    folder is gone and of outputs a recipe no longer declares, and runs,
    upstream first, every recipe that has never run, whose recipe folder no
    longer matches its record, or whose views are missing, plus everything
    downstream of those. Progress lines go to standard error; steps' own
    output appears as they run.

    Args:
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.
        all: Run every recipe, not only those that are missing or changed.
            Use it after editing the code inside a step, which sync does not
            notice by itself.

    Returns:
        A `SyncResult` saying what was removed, deleted and run.

    Raises:
        RecipeRefused: A recipe breaks a rule; nothing changed. Its
            `refusals` lists every problem found.
        StepFailed, OutputError: A recipe failed while running. The removals
            and the recipes that ran before it are kept; the exception's
            `sync_result` says what was done.
        ProjectNotFound: No project was found.
    """
    project = find_root(root)
    plan = plan_sync(project, rerun_all=all)
    for message in plan.warnings:
        warn(message)
    if plan.refusals:
        raise _combined(plan.refusals)
    collected = list(plan.warnings)

    # Remove what is no longer declared: the records first, then the files,
    # so a file that cannot be deleted is never still claimed by a record.
    views, raws = split_resources(plan.data)
    views = {p: v for p, v in views.items() if p not in plan.gone}
    write_generated(project, assemble(project, plan.kept, views, raws))
    ran: list[str] = []
    deleted: list[str] = []

    def result() -> SyncResult:
        return SyncResult(
            removed=tuple(r["name"] for r in plan.removed),
            dropped=tuple(plan.dropped),
            deleted=tuple(deleted),
            ran=tuple(ran),
            warnings=tuple(dict.fromkeys(collected)),
        )

    locked = []
    for rel in plan.to_delete:
        try:
            retry((project / rel).unlink, project / rel)
            deleted.append(rel)
        except OSError as exc:
            locked.append(f"  - {rel}: {exc.strerror or exc}")
    if locked:
        error = ProjectError(
            "The records of what is no longer declared were removed, but these view files could not be deleted:\n"
            + "\n".join(locked)
            + "\nClose any program that has them open and delete them yourself, then run useful-views sync "
            "again to make what is missing."
        )
        error.sync_result = result()
        raise error

    # Make what is missing or out of date.
    for name in plan.order:
        _write("stderr", f"useful-views: running {RECIPES}/{name}\n")
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", UsefulViewsWarning)
                outcome = run(name, root=project)
        except UsefulViewsError as exc:
            done = []
            if plan.removed or plan.dropped:
                done.append("removed the records and views that are no longer declared")
            if ran:
                done.append("run " + ", ".join(f"{RECIPES}/{n}" for n in ran))
            if done:
                exc.args = (
                    f"{exc}\n\nBefore this, sync had already {' and '.join(done)}; those changes are kept. "
                    "Fix the problem above, then run useful-views sync again.",
                    *exc.args[1:],
                )
            exc.sync_result = result()
            raise
        for w in caught:  # show each warning once across the whole sync
            message = str(w.message)
            if message not in collected:
                collected.append(message)
                warn(message)
        ran.append(name)
    return result()

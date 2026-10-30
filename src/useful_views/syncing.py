"""sync(): make the views and their records match the recipe folders.

Like `uv sync`: what is no longer declared is removed, and what is missing
or out of date is made, by actually running recipes in dependency order.
Records only ever come from real runs.

The plan (what to remove, what to run, and every rule a recipe would break)
is worked out before anything changes; `check()` reports the same plan.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .cookbooks import stale_warning
from .errors import ProjectError, RecipeRefused, UsefulViewsError, UsefulViewsWarning, warn
from .lock import project_lock
from .paths import CATALOG, DATAPACKAGE, RECIPES, WORK, exists_exact, has_content, is_file, listdir
from .project import find_root
from .recipe import Recipe, declared_paths, load_recipe, read_declaration, resolve_name
from .record import (
    assemble, differs_from_record, fingerprints_differ, input_fingerprints, known_fingerprint, load_datapackage,
    not_understood, render_catalog, render_datapackage, retry, same_record, split_resources, stale_inputs, understood,
    write_generated,
)
from .rules import Others, check_static
from .runner import _remove_outputs, _run, _write, pulled_views, rmtree


@dataclass(frozen=True)
class SyncResult:
    """What `sync` did.

    Attributes:
        removed: Recipes whose records were removed because their folder is gone.
        dropped: Views removed from a record because the recipe no longer declares them.
        deleted: Files deleted: views no longer declared, and the work/
            folders of the removed recipes (ending in "/").
        ran: Recipes run, in the order they ran.
        warnings: Warnings issued along the way (also shown as they happen).
        reasons: Why each recipe in `ran` ran: "never-run", "changed" (its
            recipe.toml or steps no longer matched its record),
            "outputs-changed", "views-missing", "input-data-changed" (a view
            it reads had been made again with different data since it ran),
            "upstream" (a view it reads was made again) or "all" (`all=True`).
    """

    removed: tuple[str, ...]
    dropped: tuple[str, ...]
    deleted: tuple[str, ...]
    ran: tuple[str, ...]
    warnings: tuple[str, ...]
    reasons: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "removed": list(self.removed),
            "dropped": list(self.dropped),
            "deleted": list(self.deleted),
            "ran": list(self.ran),
            "reasons": dict(self.reasons),
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
    # "views-missing", "input-data-changed" (a view it reads was made again with
    # different data since it ran), "upstream" (something it reads is made
    # again) or "all".
    reasons: dict[str, str] = field(default_factory=dict)
    # For each "input-data-changed" recipe, the views it reads that changed.
    stale_inputs: dict[str, list[str]] = field(default_factory=dict)
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


def _listing(items: list[str]) -> str:
    return ", ".join(items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")


def _unordered(data: dict) -> dict:
    """What datapackage.json records, with its resources and recipes in no
    particular order: the same for two files that differ only in layout."""
    def key(item: object) -> str:
        return json.dumps(item, sort_keys=True, ensure_ascii=False)

    return {**data, **{k: sorted(data[k], key=key) for k in ("resources", "recipes") if isinstance(data.get(k), list)}}


def _unjudged_notes(data: dict, gone: set[str], running: set[str]) -> list[str]:
    """Notes naming the recorded recipes whose records cannot show whether
    they were made from an older version of a view they read: records written
    by another version of useful-views, and views with no data hash. A recipe
    that runs in this sync, or whose input is made again in it, needs none."""
    views, _ = split_resources(data)
    old: set[str] = set()
    newer: set[str] = set()  # cannot be judged because a fingerprint is not understood
    nested: dict[str, tuple[str, set[str]]] = {}  # a view with no data hash: (its maker, its readers)
    for path, view in views.items():
        produced = view["producedBy"]
        reader = produced.get("recipe")
        if path in gone or reader in running:
            continue
        read = input_fingerprints(produced)
        for inp in produced.get("inputs", []):
            made = views.get(inp)
            if made is None or made["producedBy"].get("recipe") in running:
                continue  # a raw file, a view no record holds (check reports it), or one made again now
            if "fingerprint" in made and made["fingerprint"] is None:
                nested.setdefault(inp, (made["producedBy"].get("recipe"), set()))[1].add(reader)
            else:
                then, now = None if read is None else read.get(inp), made.get("fingerprint")
                if fingerprints_differ(then, now) is None:
                    strange = any(v is not None and not understood(v) for v in (then, now))
                    (newer if strange or produced.get("inputFingerprints") is not None and read is None
                     else old).add(reader)
    notes = []
    # A fingerprint in the entry of a recipe that runs now is recorded again.
    unknown = [p for p in not_understood(data)
               if p not in gone and (views.get(p) or {}).get("producedBy", {}).get("recipe") not in running]
    if unknown:
        cannot = [n for n in sorted(newer)]
        one = len(cannot) == 1
        tell = (f", so check and sync cannot tell whether {_listing([f'{RECIPES}/{n}' for n in cannot])} "
                f"{'is' if one else 'are'} out of date" if cannot else "")
        notes.append(
            f"datapackage.json holds fingerprints this version of useful-views does not understand, for "
            f"{_listing(unknown)}: they may come from a newer version of useful-views (or a hand edit). They are "
            f"ignored{tell}. If a co-author uses a newer version, upgrade useful-views; otherwise record them "
            "again with: useful-views sync --all"
        )
    if old:
        names = sorted(old)
        one = len(names) == 1
        notes.append(
            f"check and sync cannot tell whether {_listing([f'{RECIPES}/{n}' for n in names])} "
            f"{'is' if one else 'are'} out of date: datapackage.json lacks the fingerprints to show it (recorded "
            "by another version of useful-views, or when a view read had no data hash). Record them with: "
            "useful-views sync --all"
        )
    for inp, (maker, readers) in sorted(nested.items()):
        names = sorted(readers)
        one = len(names) == 1
        notes.append(
            f"{inp} has no data hash (a column of a type data hash version 1 does not cover, such as a list or "
            f"a struct), so check and sync cannot tell whether {_listing([f'{RECIPES}/{n}' for n in names])}, "
            f"which {'reads' if one else 'read'} it, {'is' if one else 'are'} out of date. Nothing needs doing "
            f"unless {RECIPES}/{maker} ran on its own (useful-views run {maker}) since {'it' if one else 'they'} "
            "last ran; if it did, "
            + (f"run it again too (useful-views run {names[0]})" if one else
               "run each of them again too (useful-views run <recipe>, upstream first)")
            + ", or use useful-views sync --all."
        )
    return notes


def _refuse_pulled_views(project: Path, plan: Plan, known: dict) -> None:
    """Refuse, before anything changes, a sync that would read a view whose file
    holds other data than its record says, made by a recipe this sync does not
    run (left by a git pull or a branch switch). A view whose maker is queued
    needs no check here: it is made again before any recipe reads it.
    That is the recipe that declares it now, or the one recorded as making it
    (a recipe renamed, or a view moved to another recipe, has a new maker)."""
    queued = set(plan.order)
    makers = {out: n for n, r in plan.loaded.items() for out in r.outputs}
    views, _ = split_resources(plan.data)
    wrong: dict[str, str] = {}
    readers: set[str] = set()
    for name in plan.order:
        for rel in plan.loaded[name].inputs:
            view = views.get(rel)
            if view is None or view["producedBy"].get("recipe") in queued or makers.get(rel) in queued:
                continue
            if rel not in wrong:
                readable, now = _on_disk(project, rel, known)
                if readable and differs_from_record(view, now):
                    wrong[rel] = view["producedBy"]["recipe"]
            if rel in wrong:
                readers.add(name)
    if wrong:
        raise ProjectError(
            f"sync would run {_listing([f'{RECIPES}/{n}' for n in sorted(readers)])}, which "
            f"{'reads' if len(readers) == 1 else 'read'} {pulled_views(wrong, plan.data)} Nothing was changed."
        )


def _on_disk(project: Path, rel: str, known: dict) -> tuple[bool, str | None]:
    """Whether the file on disk now can be read, and if so its fingerprint
    (None when it has no data hash)."""
    try:
        if not (exists_exact(project, rel) and (project / rel).is_file()):
            return False, None
        return True, known_fingerprint(project, rel, known)
    except (UsefulViewsError, OSError):
        return False, None


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
            if not (project / RECIPES / name).is_dir() or not has_content(project / RECIPES / name):
                continue  # a folder left with only hidden files counts as deleted
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
                notes.append(
                    f"{RECIPES}/{name}/ has no recipe.toml, so sync does not treat it as a recipe; check and "
                    "cookbook report it until you add one or move the folder out of recipes/."
                )
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
        changed_inputs = stale_inputs(data)
        reasons: dict[str, str] = {}
        for name, recipe in loaded.items():
            if rerun_all:
                reasons[name] = "all"
            elif name in replaced:
                reasons[name] = "outputs-changed"
            elif name not in records:
                reasons[name] = "never-run"
            elif not same_record(records[name], recipe.record()):
                reasons[name] = "changed"
            elif name in changed_inputs:
                # Before views-missing: it is known from the records alone, so
                # cookbook --check sees it in CI, where no view is on disk.
                reasons[name] = "input-data-changed"
            elif any(not exists_exact(project, o) or not (project / o).is_file() for o in recipe.outputs):
                reasons[name] = "views-missing"
        order, cycle_refusals = _run_order(loaded, set(reasons), set(records))
        refusals.extend(cycle_refusals)
        for name in order:
            reasons.setdefault(name, "upstream")
        reasons = {name: reasons[name] for name in order}

        # Recipes whose records cannot show whether they are out of date: say so,
        # and what to do.
        notes.extend(_unjudged_notes(data, set(gone), set(reasons)))

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

        removed_names = {r["name"] for r in removed}
        for name in listdir(project / WORK):
            # sync deletes the work folders of the recipes whose record it removes.
            if (project / WORK / name).is_dir() and name not in folders and name not in removed_names:
                notes.append(
                    f"{WORK}/{name}/ belongs to no recipe. If you renamed a recipe, change {WORK}/{name}/ to its new "
                    f"name in its steps (a fresh clone will not have {WORK}/{name}/); otherwise delete the folder."
                )

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
        stale_inputs={n: changed_inputs[n] for n, why in reasons.items() if why == "input-data-changed"},
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


def _later(plan: Plan) -> list[str]:
    """The recipes sync runs after another recipe it runs, which reads into
    them: their views are made again from views this sync makes again."""
    upstream, _ = _graph(plan.loaded)
    queued = set(plan.order)
    return [n for n in plan.order if set(upstream[n]) & queued]


def _stopped(plan: Plan, failed: str, ran: list[str], rerun_all: bool, interrupted: bool) -> str:
    """What to tell the user when sync stops at `failed`."""
    rest = plan.order[plan.order.index(failed):]

    def listing(names: list[str]) -> str:
        return ", ".join(f"{RECIPES}/{n}" for n in names)

    lines = [f"sync stopped at {RECIPES}/{failed}."]
    done = []
    if plan.removed or plan.dropped:
        done.append("removed the records and views that are no longer declared")
    if ran:
        done.append(f"run {listing(ran)}")
    if done:
        lines.append(f"Before this, sync had already {' and '.join(done)}; those changes are kept.")
    if rest[1:]:
        lines.append(f"It had not yet run: {listing(rest[1:])}.")
    removed = [n for n in _later(plan) if n in rest]
    if removed:
        lines.append(
            f"The views of {listing(removed)} were removed before anything ran, as they were to be made again "
            "after the recipes they read; the next sync makes them."
        )
    again = "useful-views sync --all" if rerun_all else "useful-views sync"
    if interrupted:
        lines.append(f"To finish, run: {again}")
    else:
        lines.append(f"Fix the problem above, then run: {again}")
    return "\n".join(lines)


def sync(*, root: str | Path | None = None, all: bool = False) -> SyncResult:
    """Make the views and datapackage.json match the recipe folders, like `uv sync`.

    First, every recipe folder is checked against the rules; if any recipe
    would be refused, sync raises before changing anything, listing every
    problem. Then it removes the records and view files of recipes whose
    folder is gone and of outputs a recipe no longer declares, and runs,
    upstream first, every recipe that has never run, whose recipe folder no
    longer matches its record, whose views are missing, or that read a view
    since made again with different data (`input-data-changed`), then the
    recipes downstream of those: a data hash that comes out the same does not
    show that a recipe reading the view would give the same results (a step
    can see what the hash leaves out, such as a time zone), so they run too.
    Progress lines go to standard error; steps' own output appears as they run.

    Before any recipe runs, the views of each recipe that runs after another
    one it reads from are removed: they are made again from new views. So if
    sync stops, however it stops (a failure, an interrupt, the process
    killed), those views are missing, and the next sync makes them. What it
    had already done is kept.

    Args:
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.
        all: Run every recipe, not only those that are missing or changed.
            Use it after editing the code inside a step, which sync does not
            notice by itself, and after pulling or switching branches.

    Returns:
        A `SyncResult` saying what was removed, deleted and run.

    Raises:
        RecipeRefused: A recipe breaks a rule; nothing changed. Its
            `refusals` lists every problem found.
        StepFailed, OutputError: A recipe failed while running. The removals
            and the recipes that ran before it are kept; the exception's
            `sync_result` says what was done, and its message what to run next.
        ProjectError: Another useful-views command is changing the project;
            a file could not be written or deleted; or a recipe that would run
            reads a view whose file holds other data than datapackage.json
            records (left by a git pull or a branch switch), in which case
            nothing changed and the message says what to run.
        ProjectNotFound: No project was found.
    """
    project = find_root(root)
    with project_lock(project):
        return _sync(project, all)


def _sync(project: Path, rerun_all: bool) -> SyncResult:
    plan = plan_sync(project, rerun_all=rerun_all)
    for message in plan.warnings:
        warn(message)
    if plan.refusals:
        raise _combined(plan.refusals)
    known: dict = {}  # fingerprints taken during this sync, so a view made is not hashed again when read
    _refuse_pulled_views(project, plan, known)
    collected = list(plan.warnings)

    # Remove what is no longer declared: the records first, then the files,
    # so a file that cannot be deleted is never still claimed by a record.
    views, raws = split_resources(plan.data)
    views = {p: v for p, v in views.items() if p not in plan.gone}
    data = assemble(project, plan.kept, views, raws, plan.data)
    before = {}  # as each generated file was, line endings aside (as check and git compare them)
    for rel in (DATAPACKAGE, CATALOG):
        try:
            before[rel] = (project / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            before[rel] = None
    write_generated(project, data)
    ran: list[str] = []
    deleted: list[str] = []
    # Compared with the catalog of the file as it was read, as check does: a
    # catalog that only differs in the order sync now writes is not hand-edited.
    if before[CATALOG] != render_catalog(plan.data) and not (plan.removed or plan.dropped):
        note = f"{CATALOG} did not match datapackage.json, so sync wrote it again."
        collected.append(note)
        warn(note)
    if before[DATAPACKAGE] != render_datapackage(data) and _unordered(data) == _unordered(plan.data):
        # Only the layout changed (as on the first sync after an upgrade): say
        # so, rather than leave an unexplained git diff.
        files = [rel for rel, text in ((DATAPACKAGE, render_datapackage(data)), (CATALOG, render_catalog(data)))
                 if before[rel] is not None and before[rel] != text]
        one = len(files) == 1
        note = (f"Before running anything, sync rewrote {' and '.join(files)} in this version's layout, which "
                f"changes none of what {'it records' if one else 'they record'}.")
        collected.append(note)
        warn(note)

    def result() -> SyncResult:
        return SyncResult(
            removed=tuple(r["name"] for r in plan.removed),
            dropped=tuple(plan.dropped),
            deleted=tuple(deleted),
            ran=tuple(ran),
            warnings=tuple(dict.fromkeys(collected)),
            reasons={n: plan.reasons[n] for n in ran},
        )

    deferred: list[Exception] = []

    def show(caught) -> None:  # show each warning once across the whole sync
        for w in caught:
            message = str(w.message)
            ours = issubclass(w.category, UsefulViewsWarning)
            if ours and message in collected:
                continue
            try:
                if ours:
                    collected.append(message)
                    warn(message)
                else:  # another library's warning: passed on as it was, and not one of sync's
                    warnings.warn_explicit(w.message, w.category, w.filename, w.lineno, source=w.source)
            except Exception as exc:  # warnings made errors (python -W error): raised once sync is done
                deferred.append(exc)

    locked = []
    for rel in plan.to_delete:
        try:
            retry((project / rel).unlink, project / rel)
            deleted.append(rel)
        except OSError as exc:
            locked.append(f"  - {rel}: {exc.strerror or exc}")
    # The work folders of removed recipes: scratch, and stale. A renamed recipe
    # whose steps still use the old work/ path then fails here as it would on a
    # fresh clone, instead of quietly reading old files.
    for record in plan.removed:
        work = project / WORK / record["name"]  # the name was checked when datapackage.json was read
        if work.is_dir() and not work.is_symlink() and work.resolve().parent == (project / WORK).resolve():
            try:
                rmtree(work)
                deleted.append(f"{WORK}/{record['name']}/")
            except OSError as exc:  # scratch only: worth a warning, not worth stopping
                note = f"Could not delete {WORK}/{record['name']}/ ({exc.strerror or exc}); delete it yourself."
                collected.append(note)
                warn(note)
    if locked:
        error = ProjectError(
            "The records of what is no longer declared were removed, but these could not be deleted:\n"
            + "\n".join(locked)
            + "\nClose any program that has them open and delete them yourself, then run useful-views sync "
            "again to make what is missing."
        )
        error.sync_result = result()
        raise error

    # The views of the recipes that run after another one they read from are
    # made again from new views: remove them now, so that however this sync
    # stops, even with the process killed, they are missing rather than
    # passing for current (an unchanged data hash does not show they are).
    try:
        for name in _later(plan):
            _remove_outputs(project, plan.loaded[name].outputs, "useful-views sync --all" if rerun_all
                            else "useful-views sync")
    except ProjectError as error:
        error.sync_result = result()
        raise

    # Make what is missing or out of date. Whatever stops it (a failure, an
    # interrupt, a signal between recipes), what was done is explained.
    current = None
    try:
        for name in plan.order:
            current = name
            _write("stderr", f"useful-views: running {RECIPES}/{name}\n")
            caught: list = []
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", UsefulViewsWarning)
                    _run(project, name, in_sync=True, known=known)
                ran.append(name)
            finally:
                show(caught)
            current = None
    except BaseException as exc:
        failed = current or next((n for n in plan.order if n not in ran), None)
        if failed is not None:
            note = _stopped(plan, failed, ran, rerun_all, isinstance(exc, KeyboardInterrupt))
            if isinstance(exc, (UsefulViewsError, KeyboardInterrupt)):
                exc.args = (f"{exc}\n\n{note}" if str(exc) else note, *exc.args[1:])
            else:
                exc.add_note(note)
        try:
            exc.sync_result = result()
        except AttributeError:
            pass
        raise
    if ran or plan.removed or plan.dropped:
        note = stale_warning(project)
        if note:
            collected.append(note)
            try:
                warn(note)
            except Exception as exc:
                deferred.append(exc)
    if deferred:
        raise deferred[0]
    return result()

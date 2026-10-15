"""Pre-run rules 4 to 8: how a recipe's inputs and outputs fit the project.

Rules 5 to 8 look at every other recipe two ways: what its recipe.toml
declares now, and what its record in datapackage.json says it made.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa

from .errors import RecipeRefused, UsefulViewsError, warn
from .paths import (
    RAW,
    RECIPES,
    VIEW_EXTENSIONS,
    VIEWS,
    UnreadableFolder,
    exists_exact,
    has_content,
    is_file,
    listdir,
    path_problem,
    resource_name,
)
from .recipe import Recipe, declared_paths, read_declaration
from .record import describe_raw, raw_inputs

VIEW_PATH = re.compile(rf"^{re.escape(VIEWS)}/[^/]+$")


@dataclass
class Others:
    """What the rest of the project says, as seen from one recipe."""

    declared: dict[str, tuple[list[str], list[str]]]  # name -> (inputs, outputs) from recipe.toml
    records: dict[str, dict]  # name -> record from datapackage.json
    folders: set[str]  # recipe folders on disk
    unreadable: set[str]  # recipe folders whose recipe.toml could not be read
    missing_toml: set[str] = field(default_factory=set)  # recipe folders with no recipe.toml at all


def gather_others(root: Path, data: dict, own: str, collected: list[str]) -> Others:
    declared = {}
    folders, unreadable, missing_toml = set(), set(), set()
    for name in listdir(root / RECIPES):
        if not (root / RECIPES / name).is_dir() or not has_content(root / RECIPES / name):
            continue
        folders.add(name)
        if name == own:
            continue
        try:
            has_toml = is_file(root / RECIPES / name / "recipe.toml")
        except PermissionError:
            has_toml = True  # read_declaration below reports it
        if not has_toml:
            warn(
                f"{RECIPES}/{name}/ has no recipe.toml, so it is not treated as a recipe; check and cookbook "
                "report it until you add one or move the folder out of recipes/.",
                collected,
            )
            unreadable.add(name)
            missing_toml.add(name)
            continue
        try:
            parsed = read_declaration(root, name)
        except UsefulViewsError as exc:
            warn(f"Skipped {RECIPES}/{name}/ while checking other recipes: {exc}", collected)
            unreadable.add(name)
            continue
        declared[name] = (declared_paths(parsed, "inputs"), declared_paths(parsed, "outputs"))
    records = {r["name"]: r for r in data["recipes"] if r["name"] != own}
    return Others(declared=declared, records=records, folders=folders, unreadable=unreadable,
                  missing_toml=missing_toml)


def recorded_maker_problem(others: Others, holder: str) -> str:
    """Why a record claims a view its recipe no longer declares, and the fix."""
    if holder not in others.folders:
        return "That recipe's folder no longer exists; run useful-views sync, then try again."
    if holder in others.missing_toml:
        return (
            f"{RECIPES}/{holder}/ has no recipe.toml. Restore it, or delete the folder and run useful-views sync, "
            "then try again."
        )
    if holder in others.unreadable:
        return f"{RECIPES}/{holder}/recipe.toml cannot be read; fix that file first."
    return (
        f"Its recipe.toml no longer declares it; run useful-views run {holder} so its record matches, "
        "then try again."
    )


def producers(others: Others, path: str) -> tuple[list[str], list[str]]:
    """Other recipes that make `path`: (by recipe.toml, by record)."""
    by_toml = sorted(n for n, (_, outs) in others.declared.items() if path in outs)
    by_record = sorted(n for n, r in others.records.items() if path in r["outputs"])
    return by_toml, by_record


def self_read(recipe: Recipe, rel: str) -> bool:
    """True if the recipe lists this view as both input and output (rule 6)."""
    return rel in recipe.outputs and bool(VIEW_PATH.match(rel))


def check_inputs(root: Path, recipe: Recipe, others: Others, will_run: frozenset[str] = frozenset()) -> None:
    """Rules 4 and 5. `will_run` names recipes that run before this one (in a
    sync), so a view they make need not exist or be recorded yet."""
    toml = f"{recipe.path}/recipe.toml"
    for rel in recipe.inputs:
        problem = path_problem(rel, root)
        if problem:
            raise RecipeRefused(f"In {toml}, input {problem}.", rule=4, path=toml)
        in_raw = rel.startswith(f"{RAW}/")
        if not in_raw and not VIEW_PATH.match(rel):
            raise RecipeRefused(
                f"In {toml}, input {rel!r} is not allowed: an input must be a file under {RAW}/ "
                f"or a view directly inside {VIEWS}/.",
                rule=4,
                path=toml,
            )
        if self_read(recipe, rel):
            continue
        if not in_raw:
            check_view_input(recipe, rel, others, will_run)
            if set(producers(others, rel)[0]) & will_run:
                continue  # its maker runs first and will write it
        try:
            present = exists_exact(root, rel)
        except UnreadableFolder as exc:
            raise RecipeRefused(
                f"Input {rel!r} declared in {toml} cannot be checked: {exc}", rule=4, path=toml
            ) from exc
        if not present:
            if (root / rel).exists():
                hint = (
                    " A file whose name differs only in capital letters, or in how accented letters are "
                    "encoded, exists; rename the file or change the path so they match exactly."
                )
            elif in_raw:
                hint = f" Put the file there, or correct the path in {toml}."
            else:
                maker = producers(others, rel)[0][0]
                hint = f" Run the recipe that makes it first: useful-views run {maker}"
            refusal = RecipeRefused(f"Input {rel!r} declared in {toml} does not exist.{hint}", rule=4, path=toml)
            refusal.missing_input = rel
            raise refusal
        if not (root / rel).is_file():
            raise RecipeRefused(
                f"Input {rel!r} declared in {toml} is a folder, not a file. Name a file inside it.",
                rule=4,
                path=toml,
            )
        if in_raw and (root / rel).stat().st_size == 0:
            raise RecipeRefused(
                f"Raw input {rel!r} declared in {toml} is an empty file. Replace it with the real data.",
                rule=4,
                path=toml,
            )
        if in_raw:
            check_raw_readable(root, rel, toml)


def check_raw_readable(root: Path, rel: str, toml: str) -> None:
    """A raw input can be opened, and a parquet one looks like parquet (it
    starts and ends with the bytes PAR1). Reads at most 8 bytes."""
    path = root / rel
    try:
        with open(path, "rb") as f:
            head = f.read(4)
            if rel.lower().endswith(".parquet"):
                size = f.seek(0, os.SEEK_END)
                tail = b""
                if size >= 12:  # the smallest parquet file: two markers and a footer length
                    f.seek(-4, os.SEEK_END)
                    tail = f.read(4)
                if head != b"PAR1" or tail != b"PAR1":
                    raise RecipeRefused(
                        f"Raw input {rel!r} declared in {toml} is not a valid parquet file. Replace it with a "
                        "valid parquet file, or correct the path.",
                        rule=4,
                        path=rel,
                    )
    except OSError as exc:
        raise RecipeRefused(
            f"Raw input {rel!r} declared in {toml} cannot be opened ({exc.strerror or exc}). Fix its "
            "permissions, or close the program that has it locked.",
            rule=4,
            path=rel,
        ) from exc


def check_view_input(recipe: Recipe, rel: str, others: Others, will_run: frozenset[str] = frozenset()) -> None:
    """Rule 5: a view used as input is declared, and recorded, by its maker."""
    toml = f"{recipe.path}/recipe.toml"
    by_toml, by_record = producers(others, rel)
    if not by_toml:
        if by_record:
            holder = by_record[0]
            raise RecipeRefused(
                f"Input {rel!r} declared in {toml} is recorded in datapackage.json as made by "
                f"{RECIPES}/{holder}, but no recipe.toml declares it now. "
                + recorded_maker_problem(others, holder),
                rule=5,
                path=toml,
            )
        raise RecipeRefused(
            f"Input {rel!r} declared in {toml} is not made by any recipe. A file in {VIEWS}/ can be an "
            "input only if another recipe lists it in its outputs. If it is raw data, move it to "
            f"{RAW}/ and change the path.",
            rule=5,
            path=toml,
        )
    if not any(n in by_record or n in will_run for n in by_toml):
        maker = by_toml[0]
        raise RecipeRefused(
            f"Input {rel!r} declared in {toml} is made by {RECIPES}/{maker}, which has not run "
            f"successfully with that output yet. Run it first: useful-views run {maker}",
            rule=5,
            path=toml,
        )


def check_cycle(recipe: Recipe, others: Others) -> None:
    """Rule 6: no chain of recipes leads from this recipe's outputs back to its inputs."""
    toml = f"{recipe.path}/recipe.toml"
    for rel in recipe.inputs:
        if self_read(recipe, rel):
            raise RecipeRefused(
                f"{toml} lists {rel!r} as both an input and an output. A recipe cannot read its own view; "
                "use a work file in work/ for intermediate data.",
                rule=6,
                path=toml,
            )

    def inputs_of(name: str) -> set[str]:
        found = set(others.declared.get(name, ([], []))[0])
        if name in others.records:
            found |= set(others.records[name]["inputs"])
        return found

    def makers(path: str) -> set[str]:
        by_toml, by_record = producers(others, path)
        return set(by_toml) | set(by_record)

    # Walk upstream from each input; reaching this recipe means a cycle.
    for start in recipe.inputs:
        stack = [(m, [m]) for m in sorted(makers(start))]
        seen = set()
        while stack:
            name, chain = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for upstream_input in sorted(inputs_of(name)):
                if upstream_input in recipe.outputs:
                    route = " -> ".join([recipe.name, *chain, recipe.name])
                    if upstream_input not in others.declared.get(name, ([], []))[0]:
                        raise RecipeRefused(
                            f"{toml} would create a cycle: {route} (each recipe reads a view made by the next). "
                            f"The link from {RECIPES}/{name} exists only in its record in datapackage.json, which "
                            f"still lists {upstream_input!r} as an input. " + recorded_maker_problem(others, name),
                            rule=6,
                            path=toml,
                        )
                    raise RecipeRefused(
                        f"{toml} would create a cycle: {route} (each recipe reads a view made by the next). "
                        f"{recipe.name} reads {start!r}, and further up that chain a recipe reads "
                        f"{upstream_input!r}, which {recipe.name} makes. Remove one of those inputs.",
                        rule=6,
                        path=toml,
                    )
                for m in sorted(makers(upstream_input)):
                    if m != recipe.name:
                        stack.append((m, [*chain, m]))


def check_outputs(root: Path, recipe: Recipe, others: Others) -> None:
    """Rule 7."""
    toml = f"{recipe.path}/recipe.toml"
    allowed = ", ".join(VIEW_EXTENSIONS)
    for rel in recipe.outputs:
        problem = path_problem(rel, root, example=f"{VIEWS}/my-view.parquet")
        if problem:
            raise RecipeRefused(f"In {toml}, output {problem}.", rule=7, path=toml)
        if not VIEW_PATH.match(rel):
            raise RecipeRefused(
                f"In {toml}, output {rel!r} is not allowed: an output must be a file directly inside {VIEWS}/, "
                f"like '{VIEWS}/my-view.parquet'. Intermediate files belong in work/{recipe.name}/.",
                rule=7,
                path=toml,
            )
        if rel == f"{VIEWS}/README.md":
            raise RecipeRefused(
                f"In {toml}, output {rel!r} is not allowed: useful-views writes that file itself.",
                rule=7,
                path=toml,
            )
        if not rel.endswith(VIEW_EXTENSIONS):
            raise RecipeRefused(
                f"In {toml}, output {rel!r} is not allowed: a view must end in {allowed}.",
                rule=7,
                path=toml,
            )
        if (root / rel).is_dir():
            raise RecipeRefused(
                f"Output {rel!r} declared in {toml} is a folder on disk. A view must be a single file. "
                "Delete the folder, and make the step write one file.",
                rule=7,
                path=toml,
            )
        by_toml, by_record = producers(others, rel)
        if by_toml:
            raise RecipeRefused(
                f"Output {rel!r} declared in {toml} is already declared by {RECIPES}/{by_toml[0]}/recipe.toml. "
                "Every view is made by exactly one recipe; give this one a different file name.",
                rule=7,
                path=toml,
            )
        if by_record:
            holder = by_record[0]
            raise RecipeRefused(
                f"Output {rel!r} declared in {toml} is still recorded in datapackage.json as made by "
                f"{RECIPES}/{holder}. {recorded_maker_problem(others, holder)}",
                rule=7,
                path=toml,
            )


def check_names(recipe: Recipe, others: Others) -> None:
    """Rule 8: after recording, no two resources share a name."""
    own = set(recipe.outputs) | {p for p in recipe.inputs if p.startswith(f"{RAW}/")}
    paths = set(own)
    for record in others.records.values():
        paths |= set(record["outputs"]) | set(raw_inputs(record))
    by_name: dict[str, list[str]] = {}
    for rel in sorted(paths):
        by_name.setdefault(resource_name(rel), []).append(rel)
    for name, clash in sorted(by_name.items()):
        if len(clash) > 1 and own & set(clash):  # a clash between other recipes is not this one's doing
            mine = sorted(own & set(clash))[0]
            other = next(p for p in clash if p != mine)
            fix = "Rename one of them so their names differ before the extension."
            # The other file may be only in an old record, about to be replaced.
            for holder, record in sorted(others.records.items()):
                inputs, outputs = others.declared.get(holder, ([], []))
                if (other in record["outputs"] or other in record["inputs"]) and other not in inputs + outputs:
                    if holder not in others.folders or holder in others.unreadable:
                        fix = f"{other} is only in the record of {RECIPES}/{holder}. " + recorded_maker_problem(
                            others, holder)
                    else:
                        fix = (
                            f"{other} is only in the record of {RECIPES}/{holder}, whose recipe.toml no longer "
                            f"lists it: run useful-views run {holder} first, so its record is up to date, then try "
                            "again. Or rename one of them so their names differ before the extension."
                        )
                    break
            raise RecipeRefused(
                f"Recording {recipe.path} would give two files the same resource name {name!r}: "
                f"{min(mine, other)} and {max(mine, other)}. {fix}",
                rule=8,
                path=f"{recipe.path}/recipe.toml",
            )


def describe_inputs(root: Path, recipe: Recipe) -> dict[str, dict]:
    """Describe the recipe's raw inputs before the run. An unreadable parquet is refused (rule 4)."""
    described = {}
    for rel in recipe.inputs:
        if not rel.startswith(f"{RAW}/"):
            continue
        try:
            described[rel] = describe_raw(root, rel)
        except OSError as exc:
            raise RecipeRefused(
                f"Raw input {rel!r} declared in {recipe.path}/recipe.toml cannot be opened "
                f"({exc.strerror or exc}). Fix its permissions, or close the program that has it locked.",
                rule=4,
                path=rel,
            ) from exc
        except pa.ArrowException as exc:
            raise RecipeRefused(
                f"Raw input {rel!r} declared in {recipe.path}/recipe.toml is not a valid parquet file ({exc}). "
                "Replace it with a valid parquet file, or correct the path.",
                rule=4,
                path=rel,
            ) from exc
    return described


def check_static(root: Path, recipe: Recipe, others: Others, will_run: frozenset[str] = frozenset()) -> None:
    """Rules 4 to 8 without reading raw data (at most a few bytes of each raw
    parquet), against `others`, which may describe the records as they will be
    when this recipe runs in a sync."""
    check_cycle(recipe, others)
    check_inputs(root, recipe, others, will_run)
    check_outputs(root, recipe, others)
    check_names(recipe, others)


def check_all(root: Path, recipe: Recipe, data: dict, collected: list[str]) -> tuple[Others, dict[str, dict]]:
    """Apply rules 4 to 8. Returns what the run needs to record."""
    others = gather_others(root, data, recipe.name, collected)
    check_cycle(recipe, others)  # first: it needs no files, and cycles explain missing views best
    check_inputs(root, recipe, others)
    check_outputs(root, recipe, others)
    check_names(recipe, others)
    return others, describe_inputs(root, recipe)

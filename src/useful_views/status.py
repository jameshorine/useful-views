"""check(): report the state of a project without changing anything."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import UsefulViewsError
from .paths import CATALOG, RECIPES, VIEWS, UnreadableFolder, exists_exact, listdir, slug
from .project import find_root
from .recipe import RECIPE_NAME, declared_paths, read_declaration
from .record import load_datapackage

KINDS = (
    "unreadable-recipe",
    "missing-input",
    "never-run",
    "missing-recipe-folder",
    "missing-view",
    "stray-file",
)


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    message: str

    def __str__(self) -> str:
        return self.message


def check(*, root: str | Path | None = None) -> list[Finding]:
    """Report problems with the project. An empty list means none were found."""
    project = find_root(root)
    data = load_datapackage(project)
    records = {r["name"]: r for r in data["recipes"]}
    findings: list[Finding] = []

    declared: dict[str, dict] = {}
    for name in listdir(project / RECIPES):
        if not (project / RECIPES / name).is_dir():
            continue
        rel = f"{RECIPES}/{name}/recipe.toml"
        try:
            declared[name] = read_declaration(project, name)
        except UsefulViewsError as exc:
            findings.append(Finding("unreadable-recipe", rel, str(exc).split("\n")[0]))
            continue
        if name not in records:
            if RECIPE_NAME.fullmatch(name):
                fix = f"Run it: useful-views run {name}"
            else:
                fix = (
                    "Its folder name is not allowed (use lowercase letters, digits and underscores); "
                    f"rename it, for example to {slug(name).replace('-', '_').replace('.', '_')}, then run it."
                )
            findings.append(
                Finding("never-run", f"{RECIPES}/{name}", f"{RECIPES}/{name} has never run successfully. {fix}")
            )

    for name, parsed in declared.items():
        for rel in declared_paths(parsed, "inputs"):
            try:
                if exists_exact(project, rel):
                    continue
                problem = "is missing from disk"
            except UnreadableFolder:
                problem = "is in a folder that cannot be read (permission denied)"
            makers = sorted(
                {n for n, other in declared.items() if rel in declared_paths(other, "outputs")}
                | {n for n, r in records.items() if rel in r["outputs"]}
            )
            if rel.startswith(f"{VIEWS}/") and makers:
                fix = f"Run the recipe that makes it: useful-views run {makers[0]}"
            elif rel.startswith(f"{VIEWS}/"):
                fix = f"No recipe makes it; correct the path in {RECIPES}/{name}/recipe.toml."
            else:
                fix = f"Put the file there, or correct the path in {RECIPES}/{name}/recipe.toml."
            findings.append(Finding("missing-input", rel, f"{rel}, an input of {RECIPES}/{name}, {problem}. {fix}"))

    for name, record in records.items():
        if not exists_exact(project, f"{RECIPES}/{name}"):
            findings.append(
                Finding(
                    "missing-recipe-folder",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} is recorded in datapackage.json but its folder no longer exists. "
                    "If it was removed on purpose, run useful-views sync.",
                )
            )

    for name, record in records.items():
        for rel in record["outputs"]:
            if exists_exact(project, rel):
                continue
            if exists_exact(project, f"{RECIPES}/{name}"):
                fix = f"Run it again: useful-views run {name}"
            else:
                fix = "Its folder no longer exists; if it was removed on purpose, run useful-views sync."
            findings.append(
                Finding(
                    "missing-view",
                    rel,
                    f"{rel} is recorded as made by {RECIPES}/{name} but the file is missing. {fix}",
                )
            )

    known = {p for parsed in declared.values() for p in declared_paths(parsed, "outputs")}
    known |= {p for r in records.values() for p in r["outputs"]}
    for filename in listdir(project / VIEWS):
        rel = f"{VIEWS}/{filename}"
        if rel == CATALOG or rel in known:
            continue
        if (project / rel).is_dir():
            message = (
                f"{rel} is a folder. Views must be single files directly inside {VIEWS}/; move what you need "
                "out of it and delete the folder."
            )
        else:
            message = (
                f"{rel} is a file that no recipe declares as an output. Delete it, or add it to the outputs "
                "of the recipe that makes it."
            )
        findings.append(Finding("stray-file", rel, message))

    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(findings, key=lambda f: (order[f.kind], f.path))

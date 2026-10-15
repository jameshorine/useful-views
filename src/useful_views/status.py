"""check(): report the state of a project without changing anything.

It reports what is missing or out of place, and every rule a recipe breaks
that would stop `run` or `sync`, from the same plan `sync` uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import UsefulViewsError, warn
from .paths import CATALOG, RECIPES, VIEWS, UnreadableFolder, exists_exact, listdir
from .project import find_root
from .recipe import declared_paths, read_declaration
from .record import load_datapackage
from .cookbooks import COOKBOOK, expected_text
from .syncing import plan_sync

# Every kind of finding, in the order check() reports them, with its meaning.
KIND_MEANINGS = {
    "unreadable-recipe": "A recipe folder whose recipe.toml is missing or cannot be read.",
    "refused": "A recipe breaks a rule, so run and sync would refuse it. The finding names the rule.",
    "missing-input": "An input a recipe.toml declares is not on disk.",
    "never-run": "A recipe that has never run successfully.",
    "out-of-date": "A recipe that sync would run again: its recipe.toml or steps changed since it last ran, its "
                   "outputs changed, or a view it reads will be made again.",
    "missing-recipe-folder": "datapackage.json records a recipe whose folder is gone. sync removes the record.",
    "missing-view": "A recorded view whose file is missing.",
    "stale-cookbook": "cookbook.json does not match the recorded recipes. Update it with useful-views cookbook.",
    "stray-file": "A file or folder in useful-views/, other than README.md, that no recipe makes.",
}
KINDS = tuple(KIND_MEANINGS)


@dataclass(frozen=True)
class Finding:
    """One problem `check` found.

    Attributes:
        kind: What kind of problem: one of "unreadable-recipe", "refused",
            "missing-input", "never-run", "out-of-date", "missing-recipe-folder",
            "missing-view" or "stray-file" (see `KIND_MEANINGS`).
        path: The file or folder concerned, relative to the project root.
        message: What is wrong and what to do about it.
        rule: For `refused`, the number of the broken rule; otherwise None.
        recipe: The recipe concerned, when there is one.
    """

    kind: str
    path: str
    message: str
    rule: int | None = None
    recipe: str | None = None

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "message": self.message, "rule": self.rule,
                "recipe": self.recipe}


def check(*, root: str | Path | None = None) -> list[Finding]:
    """Report the state of the project, without changing anything.

    Reports what is missing or out of place, every rule a recipe breaks that
    would stop `run` or `sync` (`refused`), and recipes that `sync` would run
    (`never-run`, `out-of-date`, `missing-view`). It does not notice changes
    inside a step's code or to a file's contents.

    Args:
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.

    Returns:
        A list of `Finding`s in a fixed order; empty when there is nothing to
        report.
    """
    project = find_root(root)
    data = load_datapackage(project)
    records = {r["name"]: r for r in data["recipes"]}
    plan = plan_sync(project)
    for message in plan.warnings:
        warn(message)
    gone = set(plan.gone)
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
    missing_inputs: set[str] = set()

    for name, parsed in declared.items():
        for rel in declared_paths(parsed, "inputs"):
            try:
                if exists_exact(project, rel):
                    continue
                problem = "is missing from disk"
                if (project / rel).exists():
                    problem = ("is not on disk with exactly that name: a file whose name differs in capital "
                               "letters, or in how accented letters are encoded, is there")
            except UnreadableFolder:
                problem = "is in a folder that cannot be read (permission denied)"
            makers = sorted(
                {n for n, other in declared.items() if rel in declared_paths(other, "outputs")}
                | {n for n, r in records.items() if rel in r["outputs"]}
            )
            if makers == [name]:
                continue  # it reads its own view: reported as a refusal (rule 6)
            missing_inputs.add(rel)
            if "capital letters" in problem:
                fix = f"Rename the file, or correct the path in {RECIPES}/{name}/recipe.toml, so they match"
            elif rel.startswith(f"{VIEWS}/") and makers:
                fix = f"{RECIPES}/{makers[0]} makes it; useful-views sync runs that recipe first"
            elif rel.startswith(f"{VIEWS}/"):
                fix = f"No recipe makes it; correct the path in {RECIPES}/{name}/recipe.toml"
            else:
                fix = f"Put the file there, or correct the path in {RECIPES}/{name}/recipe.toml"
            findings.append(
                Finding("missing-input", rel, f"{rel}, an input of {RECIPES}/{name}, {problem}. {fix}.", recipe=name)
            )

    unreadable = {f.path.split("/")[1] for f in findings if f.kind == "unreadable-recipe"}
    for refusal in plan.refusals:
        if refusal.recipe in unreadable or getattr(refusal, "missing_input", None) in missing_inputs:
            continue  # already reported more specifically
        findings.append(
            Finding("refused", refusal.path or f"{RECIPES}/{refusal.recipe}", str(refusal), rule=refusal.rule,
                    recipe=refusal.recipe)
        )

    reported = {f.recipe for f in findings if f.kind == "refused"}
    for name in declared:
        if name not in records and name not in reported:
            findings.append(
                Finding(
                    "never-run",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} has never run successfully. Make it with: useful-views sync",
                    recipe=name,
                )
            )

    why = {
        "changed": "it has changed since it last ran: its recipe.toml or step files no longer match its record "
                   "in datapackage.json",
        "outputs-changed": "its outputs changed since it last ran, so its old views are no longer declared",
        "upstream": "a view it reads will be made again by a recipe upstream of it",
    }
    for name, reason in plan.reasons.items():
        if reason in why and name not in reported:
            findings.append(
                Finding(
                    "out-of-date",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} is out of date: {why[reason]}. Bring it up to date with: useful-views sync",
                    recipe=name,
                )
            )

    for name, record in records.items():
        if not exists_exact(project, f"{RECIPES}/{name}"):
            findings.append(
                Finding(
                    "missing-recipe-folder",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} is recorded in datapackage.json but its folder no longer exists. "
                    "If it was removed on purpose, run useful-views sync.",
                    recipe=name,
                )
            )

    for name, record in records.items():
        for rel in record["outputs"]:
            if exists_exact(project, rel) or rel in gone:
                continue  # present, or no longer declared: sync removes it from the record
            if exists_exact(project, f"{RECIPES}/{name}"):
                fix = "Make it again with: useful-views sync"
            else:
                fix = "Its folder no longer exists; if it was removed on purpose, run useful-views sync."
            findings.append(
                Finding(
                    "missing-view",
                    rel,
                    f"{rel} is recorded as made by {RECIPES}/{name} but the file is missing. {fix}",
                    recipe=name,
                )
            )

    cookbook_file = project / COOKBOOK
    if cookbook_file.is_file() and cookbook_file.read_text(encoding="utf-8") != expected_text(project):
        findings.append(
            Finding(
                "stale-cookbook",
                COOKBOOK,
                f"{COOKBOOK} does not match the recorded recipes. Once the project is in sync, update it "
                "with: useful-views cookbook",
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

"""check(): report the state of a project without changing anything.

It reports what is missing or out of place, and every rule a recipe breaks
that would stop `run` or `sync`, from the same plan `sync` uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ProjectError, UsefulViewsError, warn
from .paths import CATALOG, RECIPES, VIEWS, UnreadableFolder, exists_exact, has_content, listdir
from .project import find_root
from .recipe import declared_paths, read_declaration
from .record import load_datapackage, render_catalog
from .cookbooks import COOKBOOK, read_cookbook, build, render
from .syncing import plan_sync

# Every kind of finding, in the order check() reports them, with its meaning.
KIND_MEANINGS = {
    "unreadable-recipe": "A recipe folder whose recipe.toml is missing or cannot be read.",
    "refused": "A recipe breaks a rule, so run and sync would refuse it. The finding names the rule.",
    "missing-input": "An input a recipe.toml declares is not on disk.",
    "never-run": "A recipe that has never run successfully.",
    "out-of-date": "A recipe whose record no longer matches: its recipe.toml or steps changed since it last ran, "
                   "its outputs changed, a view it reads was made again with different data since it ran, or a "
                   "view it reads will be made again. sync runs it, except that a view dropped from its outputs "
                   "only has its record removed.",
    "missing-recipe-folder": "datapackage.json records a recipe whose folder is gone. sync removes the record.",
    "missing-view": "A recorded view whose file is missing.",
    "stale-cookbook": "cookbook.json does not match the recorded recipes. Update it with useful-views cookbook.",
    "stale-catalog": "useful-views/README.md does not match datapackage.json (edited by hand, or left from a "
                     "merge). useful-views sync writes it again.",
    "stray-file": "A file or folder in useful-views/, other than README.md, that no recipe makes.",
}
KINDS = tuple(KIND_MEANINGS)

# Kinds that `useful-views sync` resolves by itself.
SYNCABLE = frozenset({"never-run", "out-of-date", "missing-view", "missing-recipe-folder", "stale-catalog"})


def syncable(finding: "Finding") -> bool:
    """True if `useful-views sync` resolves this finding by itself."""
    return finding.kind in SYNCABLE or (finding.kind == "missing-input" and finding.reason == "made-by-recipe")


@dataclass(frozen=True)
class Finding:
    """One problem `check` found.

    Attributes:
        kind: What kind of problem: one of "unreadable-recipe", "refused",
            "missing-input", "never-run", "out-of-date", "missing-recipe-folder",
            "missing-view", "stale-cookbook", "stale-catalog" or "stray-file".
            The check findings reference lists what each means.
        path: The file or folder concerned, relative to the project root.
        message: What is wrong and what to do about it.
        rule: For `refused`, the number of the broken rule; otherwise None.
        recipe: The recipe concerned, when there is one.
        reason: For `out-of-date`, why: "changed" (its recipe.toml or steps
            no longer match its record), "outputs-changed" (it no longer
            declares any view it made), "outputs-dropped" (it no longer
            declares some of them; sync removes them without running it),
            "input-data-changed" (a view it reads was made again with
            different data since it ran: its recorded fingerprint no longer
            matches) or "upstream" (a view it reads will be made again). For
            `missing-input`, "made-by-recipe" when the input is a view
            another recipe makes, which sync makes first. Otherwise None.
    """

    kind: str
    path: str
    message: str
    rule: int | None = None
    recipe: str | None = None
    reason: str | None = None

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "message": self.message, "rule": self.rule,
                "recipe": self.recipe, "reason": self.reason}


def check(*, root: str | Path | None = None) -> list[Finding]:
    """Report the state of the project, without changing anything.

    Reports what is missing or out of place, every rule a recipe breaks that
    would stop `run` or `sync` (`refused`), and recipes that `sync` would run
    (`never-run`, `out-of-date`, `missing-view`). It does not notice changes
    inside a step's code or to a raw file until that recipe runs again, nor
    view files on disk that no longer match their record (as after a
    `git pull`).

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
        if not (project / RECIPES / name).is_dir() or not has_content(project / RECIPES / name):
            continue
        rel = f"{RECIPES}/{name}/recipe.toml"
        try:
            declared[name] = read_declaration(project, name)
        except UsefulViewsError as exc:
            findings.append(Finding("unreadable-recipe", rel, str(exc).split("\n")[0], recipe=name))
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
            made = rel.startswith(f"{VIEWS}/") and bool(makers) and "capital letters" not in problem
            findings.append(
                Finding("missing-input", rel, f"{rel}, an input of {RECIPES}/{name}, {problem}. {fix}.", recipe=name,
                        reason="made-by-recipe" if made else None)
            )

    unreadable = {f.recipe for f in findings if f.kind == "unreadable-recipe"}
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
        if reason == "input-data-changed" and name not in reported:
            paths = plan.stale_inputs.get(name, [])
            findings.append(
                Finding(
                    "out-of-date",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} is out of date: {', '.join(paths)} {'was' if len(paths) == 1 else 'were'} "
                    "made again with different data since it ran. Bring it up to date with: useful-views sync. If "
                    "that data should not have changed, make sure the recipe that writes it sorts its rows by columns "
                    "that together identify each row (such as .sort(\"iso\", \"year\")): the same rows in another "
                    "order have another fingerprint.",
                    recipe=name,
                    reason=reason,
                )
            )
        elif reason in why and name not in reported:
            findings.append(
                Finding(
                    "out-of-date",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} is out of date: {why[reason]}. Bring it up to date with: useful-views sync",
                    recipe=name,
                    reason=reason,
                )
            )

    def folder_present(name: str) -> bool:
        return exists_exact(project, f"{RECIPES}/{name}") and has_content(project / RECIPES / name)

    makers = {out: n for n, r in plan.loaded.items() for out in r.outputs}
    for name, record in records.items():
        loaded = plan.loaded.get(name)
        if loaded is None or name in plan.reasons or name in reported:
            continue
        dropped = [o for o in record["outputs"] if o not in loaded.outputs]
        if dropped:
            one = len(dropped) == 1
            moved = {o: makers[o] for o in dropped if o in makers}
            then = f"useful-views sync removes {'it' if one else 'them'} from the record"
            if len(moved) < len(dropped):
                then += f" and deletes {'the file' if one else ', '.join(o for o in dropped if o not in moved)}"
            then += "".join(f"; {o} is now made by {RECIPES}/{m}" for o, m in moved.items())
            findings.append(
                Finding(
                    "out-of-date",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} no longer declares {', '.join(dropped)}, which its record in datapackage.json "
                    f"still lists. {then}.",
                    recipe=name,
                    reason="outputs-dropped",
                )
            )

    for name, record in records.items():
        if not folder_present(name):
            findings.append(
                Finding(
                    "missing-recipe-folder",
                    f"{RECIPES}/{name}",
                    f"{RECIPES}/{name} is recorded in datapackage.json but its folder no longer exists (or holds "
                    "only hidden files). If it was removed on purpose, run useful-views sync.",
                    recipe=name,
                )
            )

    for name, record in records.items():
        for rel in record["outputs"]:
            if (exists_exact(project, rel) and (project / rel).is_file()) or rel in gone:
                continue  # present, or no longer declared: sync removes it from the record
            if folder_present(name):
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

    try:
        current = read_cookbook(project)
        stale = current is not None and current != render(build(data))
        problem = "does not match the recorded recipes"
    except ProjectError as exc:
        stale, problem = True, f"cannot be read ({exc})"
    try:
        catalog = (project / CATALOG).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        catalog = None
    if catalog != render_catalog(data):
        findings.append(
            Finding(
                "stale-catalog",
                CATALOG,
                f"{CATALOG} does not match datapackage.json: it was edited by hand, deleted, or left from a "
                "merge. It is generated; useful-views sync writes it again.",
            )
        )

    if stale:
        findings.append(
            Finding(
                "stale-cookbook",
                COOKBOOK,
                f"{COOKBOOK} {problem}. Once the project is in sync, update it with: useful-views cookbook",
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

"""Reading a recipe folder: recipe.toml, with the steps it lists, and the step files.

This module only reads. It checks the recipe's own format (rules 1 to 3
under "Rules" in the README).
"""

from __future__ import annotations

import os
import posixpath
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RecipeRefused, warn
from .paths import IGNORED_NAMES, RECIPES, WINDOWS_RESERVED, is_file, listdir

RECIPE_NAME = re.compile(r"^[a-z0-9_]+$")
STEP_FILE = re.compile(r"^([0-9]{2})_(.+)\.py$")
OTHER_LANGUAGE_STEP = re.compile(r"^[0-9]{2}_.+\.(r|ipynb|rmd|qmd|jl|do)$", re.IGNORECASE)
STEP_FORM = "step files are named with exactly two digits, an underscore and a name ending in .py, like 01_clean.py"
KNOWN_KEYS = ("description", "inputs", "outputs", "steps")
STEP_KEYS = ("file", "description")
# Keys of recipe.toml itself that, written below a [[steps]] header, land in that step.
TOP_LISTS = ("inputs", "outputs")
STEPS_HEADER = re.compile(r"^\s*\[\[\s*steps\s*\]\]")
KEY_LINE = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")
ERROR_LINE = re.compile(r"\(at line (\d+)")
TODO = "TODO"
STEP_TODO = "TODO: one sentence saying what this step does."


@dataclass(frozen=True)
class Step:
    name: str
    path: str
    description: str


@dataclass(frozen=True)
class Recipe:
    name: str
    description: str
    path: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    steps: tuple[Step, ...]
    warnings: tuple[str, ...] = field(default=(), compare=False)

    def record(self) -> dict:
        """The recipe's entry in datapackage.json's `recipes` list."""
        return {
            "name": self.name,
            "description": self.description,
            "path": self.path,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "steps": [
                {"name": s.name, "path": s.path, "description": s.description}
                for s in self.steps
            ],
        }


def collapse(text: str) -> str:
    return " ".join(text.split())


def read_text(path: Path, rel: str, rule: int) -> str:
    """Read a text file as UTF-8, tolerating a byte-order mark and CRLF."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RecipeRefused(f"Cannot read {rel}: {exc.strerror}.", rule=rule, path=rel) from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RecipeRefused(
            f"{rel} is not saved as UTF-8. Open it in your editor and save it with UTF-8 encoding.",
            rule=rule,
            path=rel,
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


# --- recipe.toml ------------------------------------------------------------


def _toml_hint(text: str, error: str) -> str:
    """Advice for the TOML mistakes most often made in recipe.toml, judged
    from the key whose value the error is in (the nearest `key =` line at or
    above the error) and whether that key sits below a [[steps]] header."""
    lines = text.split("\n")
    match = ERROR_LINE.search(error)
    at = int(match.group(1)) - 1 if match else len(lines) - 1
    at = min(max(at, 0), len(lines) - 1)
    key, key_at = None, at
    for i in range(at, -1, -1):
        if found := KEY_LINE.match(lines[i]):
            key, key_at = found.group(1), i
            break
    in_steps = any(STEPS_HEADER.match(line) for line in lines[:key_at])
    if "Cannot overwrite a value" in error and key:
        if in_steps and key == "description":
            return (f" Every line below a [[steps]] header belongs to that step, so the description on line "
                    f"{key_at + 1} is read as that step's second one. If it is the recipe's description, move it "
                    "above the first [[steps]].")
        return f" `{key}` is set twice{' in one [[steps]] block' if in_steps else ''}: keep one."
    if key == "description" and ("\\" in error or "escape" in error.lower()):
        return (" In a description, a backslash starts an escape sequence: write it as \\\\, or put the text in "
                "triple single quotes, description = '''...''', which keep backslashes as they are.")
    if key == "description" and '"""' not in lines[key_at] and lines[at].count('"') > 2:
        return ' A description with double quotes in it needs triple quotes: description = """...""".'
    if "\\" in text and ("escape" in error.lower() or "\\" in error):
        return ' Paths in recipe.toml use forward slashes, like "raw/my_data.csv", not backslashes.'
    return ""


def read_declaration(root: Path, name: str) -> dict:
    """Parse recipes/<name>/recipe.toml into a dict. Raises RecipeRefused (rule 1)."""
    rel = f"{RECIPES}/{name}/recipe.toml"
    path = root / RECIPES / name / "recipe.toml"
    try:
        found = is_file(path)
    except PermissionError as exc:
        raise RecipeRefused(
            f"Cannot read {RECIPES}/{name}/: permission denied. Fix the folder's permissions.", rule=1, path=rel
        ) from exc
    if not found:
        raise RecipeRefused(
            f"{rel} is missing. Every recipe folder needs a recipe.toml with a "
            "description, inputs, outputs and steps.",
            rule=1,
            path=rel,
        )
    text = read_text(path, rel, rule=1)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        hint = _toml_hint(text, str(exc))
        raise RecipeRefused(
            f"{rel} is not valid TOML: {exc}. Fix the syntax in that file.{hint}", rule=1, path=rel
        ) from exc


def declared_paths(data: dict, key: str) -> list[str]:
    """The string paths under `key`, ignoring anything malformed. For reading
    other recipes, where malformed entries are not this recipe's problem."""
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [p for p in value if isinstance(p, str)]


def _steps_example(step_files: Sequence[str]) -> str:
    """How to list the steps, with a [[steps]] block ready to paste for each step file."""
    blocks = "\n\n".join(
        f'      [[steps]]\n      file = "{file}"\n      description = "{STEP_TODO}"'
        for file in (step_files or ["01_first_step.py"])
    )
    return (
        "list each step file at the end of recipe.toml, in the order they run, and replace each TODO with "
        f"one sentence saying what that step does:\n\n{blocks}"
    )


def _check_steps(data: dict, step_files: Sequence[str], problems: list[str],
                 unknown: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Rule 1 for `steps`: a [[steps]] block for each step, each with a file
    name and a description. Appends to `problems`, and each unknown key to
    `unknown` as (block, key); returns the (file, description) pairs."""
    value = data.get("steps")
    if "steps" not in data:
        problems.append("`steps` is missing: " + _steps_example(step_files))
        return []
    if isinstance(value, dict):
        problems.append("`steps` is written [steps], with one pair of square brackets: write [[steps]], with two, "
                        "above each step's file and description")
        return []
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        problems.append('`steps` must be a [[steps]] block for each step, each with file = "01_name.py" and '
                        'description = "One sentence saying what this step does."')
        return []
    if not value:
        problems.append("`steps` is empty: " + _steps_example(step_files))
        return []
    steps: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, entry in enumerate(value, 1):
        file = entry.get("file")
        named = isinstance(file, str) and bool(file.strip())
        block = f"the [[steps]] block for {file}" if named else f"[[steps]] block {number}"
        for key in TOP_LISTS:
            if key in entry:
                problems.append(f"`{key}` is inside {block}: every line below a [[steps]] header belongs to that "
                                f"step. Move `{key}` above the first [[steps]]")
        if "file" not in entry:
            problems.append(f'{block} has no `file`: add file = "NN_name.py", the name of the step file')
        elif not named:
            problems.append(f"{block}: `file` must be the step file's name in double quotes, like "
                            'file = "01_clean.py"')
        elif "/" in file or "\\" in file:
            bare = file.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            problems.append(f"{block}: `file` is the name of a file in this recipe's folder, not a path: "
                            f'write file = "{bare}"')
        elif file in seen:
            problems.append(f"`steps` lists {file!r} twice: keep one")
        if named:
            seen.add(file)
        text = entry.get("description")
        if "description" not in entry:
            problems.append(f'{block} has no `description`: add description = "One sentence saying what this '
                            'step does."')
        elif not isinstance(text, str) or not collapse(text):
            problems.append(f"{block}: `description` must be a non-empty string")
        elif collapse(text).startswith(TODO):
            problems.append(f"the description in {block} still starts with TODO: replace it "
                            "with one sentence saying what this step does")
        unknown.extend((block, key) for key in entry if key not in STEP_KEYS and key not in TOP_LISTS)
        if named and isinstance(text, str):
            steps.append((file, collapse(text)))
    return steps


def check_declaration(data: dict, rel: str, step_files: Sequence[str] = ()
                      ) -> tuple[str, list[str], list[str], list[tuple[str, str]], list[str]]:
    """Apply rule 1 to a parsed recipe.toml. Returns description, inputs,
    outputs, the steps as (file, description) pairs, and warnings. All rule 1
    problems are reported together. `step_files`, the files in the folder
    named like steps, only shape the advice when `steps` is missing."""
    problems: list[str] = []
    found: list[str] = []
    entries = data.get("steps") if isinstance(data.get("steps"), list) else []
    # A list written below a [[steps]] header: reported there, not as missing.
    misplaced = {key for entry in entries if isinstance(entry, dict) for key in TOP_LISTS if key in entry}

    description = data.get("description")
    if "description" not in data:
        where = " above the first [[steps]]" if "steps" in data else ""
        problems.append('`description` is missing: add description = "One sentence saying what this recipe '
                        f'produces."{where}')
    elif not isinstance(description, str) or not collapse(description):
        problems.append("`description` must be a non-empty string")
    elif collapse(description).startswith(TODO):
        problems.append("`description` still starts with TODO: replace it with one sentence saying what this recipe produces")

    lists = {}
    for key in ("inputs", "outputs"):
        value = data.get(key)
        example = '["raw/my_data.csv"]' if key == "inputs" else '["useful-views/my-view.parquet"]'
        if key not in data:
            if key not in misplaced:
                problems.append(f"`{key}` is missing: add {key} = {example}")
        elif not isinstance(value, list) or not all(isinstance(p, str) for p in value):
            problems.append(f"`{key}` must be a list of paths in double quotes, like {key} = {example}")
        elif not value:
            problems.append(f"`{key}` is empty: list at least one path, like {key} = {example}")
        else:
            seen = set()
            for p in value:
                if p in seen:
                    problems.append(f"`{key}` lists {p!r} twice: keep one")
                seen.add(p)
            lists[key] = list(value)

    step_unknown: list[tuple[str, str]] = []
    steps = _check_steps(data, step_files, problems, step_unknown)

    unknown = [key for key in data if key not in KNOWN_KEYS]
    if problems:
        if unknown:
            problems.append(
                "it has keys useful-views does not read: "
                + ", ".join(f"`{k}`" for k in unknown)
                + " (check the spelling: the keys are description, inputs, outputs and steps)"
            )
        for block, key in step_unknown:
            problems.append(f"{block} has a key useful-views does not read: `{key}` (check the spelling: a step "
                            "has a file and a description)")
        raise RecipeRefused(f"{rel} needs fixing:\n" + "\n".join(f"  - {p}" for p in problems), rule=1, path=rel)

    for key in unknown:
        found.append(
            f"{rel} has an unknown key `{key}`, which useful-views ignores. "
            f"It only reads {', '.join(KNOWN_KEYS)}."
        )
    for block, key in step_unknown:
        found.append(f"{rel}: {block} has an unknown key `{key}`, which useful-views ignores. "
                     "A step only has a file and a description.")
    return collapse(description), lists["inputs"], lists["outputs"], steps, found


# --- step files -------------------------------------------------------------


def _near_miss(filename: str) -> str | None:
    """Why a file named almost like a step is not one, or None if it does not
    look like a step at all (notes, data, editor backups such as .bak or ~)."""
    if not re.match(r"^[0-9]+", filename):
        return None
    lower = filename.lower()
    if re.search(r"\.py\.(txt|rtf)$", lower):
        return (f"its name ends in {filename[-4:]} after .py, which an editor added (on Windows, turn on File name "
                f"extensions in Explorer's View menu to see it); {STEP_FORM}.")
    if lower.endswith(".py"):
        if not filename.endswith(".py"):
            return f"its .py is not in lowercase; {STEP_FORM}."
        if " " in filename:
            return f"its name has a space; {STEP_FORM}."
        return f"{STEP_FORM}."
    if "." not in filename and re.match(r"^[0-9]+[ _-]", filename):
        return f"it has no .py extension; {STEP_FORM}."
    return None


def step_files(root: Path, name: str) -> list[str]:
    """The files in recipes/<name>/ named like steps, in the order they run."""
    folder = root / RECIPES / name
    return [f for f in listdir(folder) if STEP_FILE.fullmatch(f) and (folder / f).is_file()]


def find_steps(root: Path, name: str, listed: Sequence[tuple[str, str]], collected: list[str]) -> list[Step]:
    """Apply rules 3 and 2: the step files in recipes/<name>/ are Python
    files, no two share a number, and they are exactly the files recipe.toml
    lists, in the order of their numbers. `listed` holds the (file,
    description) pairs from recipe.toml."""
    folder = root / RECIPES / name
    listed_files = [file for file, _ in listed]
    files: list[str] = []
    candidates: list[tuple[str, str, str]] = []
    for filename in listdir(folder):
        rel = f"{RECIPES}/{name}/{filename}"
        if not (folder / filename).is_file():
            continue
        files.append(filename)
        if OTHER_LANGUAGE_STEP.fullmatch(filename):
            if filename.endswith(".ipynb"):
                why = "Notebooks cannot be steps yet. Export it as a .py script (in Jupyter: File > Save and Export Notebook As > Executable Script), name it like 01_name.py, list it in recipe.toml, and remove the notebook from the recipe folder."
            else:
                why = "Steps must be Python (.py) files. Rewrite it as a .py file, or move it out of the recipe folder."
            raise RecipeRefused(f"{rel} is named like a step but is not a .py file. {why}", rule=3, path=rel)
        match = STEP_FILE.fullmatch(filename)
        if match:
            candidates.append((match.group(1), match.group(2), filename))
        elif filename not in listed_files and (why := _near_miss(filename)) is not None:
            warn(f"{rel} is not run: {why} Rename it, and list it in recipe.toml, if it is meant to be a step.",
                 collected)

    by_number: dict[str, str] = {}
    for number, _, filename in candidates:
        if number in by_number:
            raise RecipeRefused(
                f"Two steps in {RECIPES}/{name}/ share the number {number}: {by_number[number]} and {filename}. "
                "Renumber one so each step has its own number, and change its file = in recipe.toml.",
                rule=2,
                path=f"{RECIPES}/{name}/{filename}",
            )
        by_number[number] = filename

    on_disk = {filename for _, _, filename in candidates}
    by_lower = {f.lower(): f for f in files}
    problems: list[tuple[str, str]] = []  # (path to fix, what is wrong)
    for file in listed_files:
        rel = f"{RECIPES}/{name}/{file}"
        if file in on_disk:
            continue
        if file in files:
            why = _near_miss(file) or f"{STEP_FORM}."
            problems.append((rel, f"{rel} is listed, but it is not named like a step: {why}"))
        elif file.lower() in by_lower:
            problems.append((rel, f"{rel} is listed, but the file is named {by_lower[file.lower()]}: make the two "
                                  "match, letter case included"))
        else:
            problems.append((rel, f"{rel} is listed, but there is no such file"))
    listed_lower = {file.lower() for file in listed_files}
    in_order = [filename for _, _, filename in sorted(candidates)]
    for filename in in_order:
        if filename.lower() not in listed_lower:
            problems.append((f"{RECIPES}/{name}/{filename}",
                             f"{RECIPES}/{name}/{filename} is named like a step, but recipe.toml does not list it: "
                             "add a [[steps]] block for it, or move it out of the recipe folder if it is not a step"))
    if not problems and listed_files != in_order:
        problems.append((f"{RECIPES}/{name}/recipe.toml",
                         "the steps are listed in a different order from their numbers, which is the order they "
                         "run: list them as " + ", ".join(in_order)))
    if problems:
        raise RecipeRefused(
            f"The steps listed in {RECIPES}/{name}/recipe.toml do not match the step files in its folder:\n"
            + "\n".join(f"  - {text}" for _, text in problems),
            rule=2,
            path=problems[0][0],
        )
    return [
        Step(name=STEP_FILE.fullmatch(file).group(2), path=f"{RECIPES}/{name}/{file}", description=description)
        for file, description in listed
    ]


# --- whole recipe -----------------------------------------------------------


def recipe_name_problem(name: str) -> str | None:
    """Why a recipe folder name is not allowed, or None."""
    if not RECIPE_NAME.fullmatch(name):
        return "use only lowercase letters, digits and underscores, like pwt_panel"
    if WINDOWS_RESERVED.match(name):
        return (f"{name} is a reserved device name on Windows, so the folder could not be checked out there; "
                f"choose another, like {name}_data")
    if name in IGNORED_NAMES:
        return f"{name} is a folder name useful-views ignores; choose another, like {name.strip('_')}_data"
    return None


def resolve_name(root: Path, recipe: str) -> str:
    """Accept 'name', 'recipes/name', './recipes/name/', '.\\recipes\\name\\' or
    the folder's full path; require exact case."""
    name = recipe.replace("\\", "/").strip()
    if os.path.isabs(recipe.strip()):
        try:
            name = Path(recipe.strip()).resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            pass  # outside the project: refused below as no such recipe
    if name not in ("", ".", "/"):
        name = posixpath.normpath(name)
    if name.startswith(f"{RECIPES}/"):
        name = name[len(RECIPES) + 1 :]
    name = name.rstrip("/")
    existing = [n for n in listdir(root / RECIPES) if (root / RECIPES / n).is_dir()]
    if name not in existing:
        listing = ", ".join(existing) if existing else "none yet"
        first = name.split("/")[0]
        guess = next(
            (n for n in existing if n == first or n.lower() == name.lower().replace("-", "_")), None
        )
        if guess and "/" in name:
            hint = f"run takes a recipe name, not a file: useful-views run {guess}"
        elif guess:
            hint = f"Did you mean {guess}?"
        else:
            hint = "Create one with useful-views new-recipe <name>."
        raise RecipeRefused(
            f"There is no recipe folder {RECIPES}/{name}. Existing recipes: {listing}. {hint}",
            path=f"{RECIPES}/{name}" + ("" if name.endswith(".py") else "/"),
        )
    problem = recipe_name_problem(name)
    if problem:
        raise RecipeRefused(
            f"The recipe folder name {name!r} is not allowed: {problem}. Rename {RECIPES}/{name}/.",
            path=f"{RECIPES}/{name}/",
        )
    return name


def load_recipe(root: Path, name: str) -> Recipe:
    """Read and check one recipe (rules 1 to 3)."""
    collected: list[str] = []
    rel = f"{RECIPES}/{name}/recipe.toml"
    data = read_declaration(root, name)
    description, inputs, outputs, listed, found = check_declaration(data, rel, step_files(root, name))
    for message in found:
        warn(message, collected)
    steps = find_steps(root, name, listed, collected)
    return Recipe(
        name=name,
        description=description,
        path=f"{RECIPES}/{name}",
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        steps=tuple(steps),
        warnings=tuple(collected),
    )

"""Reading a recipe folder: recipe.toml, its step files and their descriptions.

This module only reads. It checks the recipe's own format (rules 1 to 3
under "Rules" in the README).
"""

from __future__ import annotations

import os
import posixpath
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RecipeRefused, warn
from .paths import IGNORED_NAMES, RECIPES, WINDOWS_RESERVED, is_file, listdir

RECIPE_NAME = re.compile(r"^[a-z0-9_]+$")
STEP_FILE = re.compile(r"^([0-9]{2})_(.+)\.py$")
OTHER_LANGUAGE_STEP = re.compile(r"^[0-9]{2}_.+\.(r|ipynb|rmd|qmd|jl|do)$", re.IGNORECASE)
# A comment line that is only decoration, such as "# -----" or "# =====".
RULE_LINE = re.compile(r"^#\s*([-=*#~_])\1{2,}\s*$")
PEP723_START = re.compile(r"^# /// [a-zA-Z0-9-]+$")
KNOWN_KEYS = ("description", "inputs", "outputs")
TODO = "TODO"


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
            "description, inputs and outputs.",
            rule=1,
            path=rel,
        )
    text = read_text(path, rel, rule=1)
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        hint = ""
        if "\\" in text and ("escape" in str(exc).lower() or "\\" in str(exc)):
            hint = ' Paths in recipe.toml use forward slashes, like "raw/my_data.csv", not backslashes.'
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


def check_declaration(data: dict, rel: str) -> tuple[str, list[str], list[str], list[str]]:
    """Apply rule 1 to a parsed recipe.toml. Returns description, inputs,
    outputs and warnings. All rule 1 problems are reported together."""
    problems: list[str] = []
    found: list[str] = []

    description = data.get("description")
    if "description" not in data:
        problems.append('`description` is missing: add description = "One sentence saying what this recipe produces."')
    elif not isinstance(description, str) or not collapse(description):
        problems.append("`description` must be a non-empty string")
    elif collapse(description).startswith(TODO):
        problems.append("`description` still starts with TODO: replace it with one sentence saying what this recipe produces")

    lists = {}
    for key in ("inputs", "outputs"):
        value = data.get(key)
        example = '["raw/my_data.csv"]' if key == "inputs" else '["useful-views/my-view.parquet"]'
        if key not in data:
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

    unknown = [key for key in data if key not in KNOWN_KEYS]
    if problems:
        if unknown:
            problems.append(
                "it has keys useful-views does not read: "
                + ", ".join(f"`{k}`" for k in unknown)
                + " (check the spelling: the keys are description, inputs and outputs)"
            )
        raise RecipeRefused(f"{rel} needs fixing:\n" + "\n".join(f"  - {p}" for p in problems), rule=1, path=rel)

    for key in unknown:
        found.append(
            f"{rel} has an unknown key `{key}`, which useful-views ignores. "
            f"It only reads {', '.join(KNOWN_KEYS)}."
        )
    return collapse(description), lists["inputs"], lists["outputs"], found


# --- step files -------------------------------------------------------------


def _jupytext_header(lines: list[str], start: int) -> bool:
    """True if the "# ---" at `start` opens a Jupytext header: comment lines
    up to a closing "# ---", one of them a "jupyter:" or "jupytext:" key."""
    for line in lines[start + 1:]:
        line = line.strip()
        if line == "# ---":
            return False
        if not line.startswith("#"):
            return False
        if re.match(r"^#\s*(jupyter|jupytext):", line):
            return True
    return False


def step_description(text: str) -> str:
    """The top comment block of a step file, joined into one line.

    Leading blank lines and machine lines are skipped first: a shebang, a
    coding line, notebook cell markers, a Jupytext header, an inline script
    metadata block (`# /// script` ... `# ///`, PEP 723) and decoration lines
    such as `# -----`. A decoration line after the description ends it.
    """
    lines = text.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
        elif i == 0 and line.startswith("#!"):
            i += 1
        elif i <= 1 and re.match(r"^#.*coding[:=]\s*[-\w.]+", line):
            i += 1
        elif re.match(r"^#\s*(%%|In\[.*\]:)", line):
            i += 1
        elif line == "# ---" and _jupytext_header(lines, i):
            i += 1  # a Jupytext header, up to its closing "# ---"
            while i < n and lines[i].strip() != "# ---":
                i += 1
            i += 1
        elif PEP723_START.match(line):
            i += 1  # inline script metadata, up to its closing "# ///"
            while i < n and lines[i].strip() != "# ///":
                i += 1
            i += 1
        elif RULE_LINE.match(line):
            i += 1
        else:
            break
    parts = []
    while i < n and lines[i].lstrip().startswith("#"):
        line = lines[i].strip()
        if re.match(r"^#\s*(%%|In\[.*\]:)", line) or RULE_LINE.match(line) or PEP723_START.match(line):
            break  # a notebook cell marker or a decoration line ends the description
        after_shebang = i > 0 and lines[i - 1].strip().startswith("#!")
        if line.startswith("#!") or (after_shebang and re.match(r"^#.*coding[:=]\s*[-\w.]+", line)):
            i += 1
            continue  # a shebang (and its coding line) is not part of the description
        parts.append(re.sub(r"^[#\s]+", "", line))
        i += 1
    return collapse(" ".join(p for p in parts if p))


def _near_miss(filename: str) -> str | None:
    """Why a file named almost like a step is not one, or None if it does not
    look like a step at all (notes, data, editor backups such as .bak or ~)."""
    if not re.match(r"^[0-9]+", filename):
        return None
    form = "step files are named with exactly two digits, an underscore and a name ending in .py, like 01_clean.py"
    lower = filename.lower()
    if re.search(r"\.py\.(txt|rtf)$", lower):
        return (f"its name ends in {filename[-4:]} after .py, which an editor added (on Windows, turn on File name "
                f"extensions in Explorer's View menu to see it); {form}.")
    if lower.endswith(".py"):
        if not filename.endswith(".py"):
            return f"its .py is not in lowercase; {form}."
        if " " in filename:
            return f"its name has a space; {form}."
        return f"{form}."
    if "." not in filename and re.match(r"^[0-9]+[ _-]", filename):
        return f"it has no .py extension; {form}."
    return None


def find_steps(root: Path, name: str, collected: list[str]) -> list[Step]:
    """Apply rules 3 and 2 to the files in recipes/<name>/."""
    folder = root / RECIPES / name
    candidates: list[tuple[str, str, str]] = []
    for filename in listdir(folder):
        rel = f"{RECIPES}/{name}/{filename}"
        if not (folder / filename).is_file():
            continue
        if OTHER_LANGUAGE_STEP.fullmatch(filename):
            if filename.endswith(".ipynb"):
                why = "Notebooks cannot be steps yet. Export it as a .py script (in Jupyter: File > Save and Export Notebook As > Executable Script), name it like 01_name.py, and remove the notebook from the recipe folder."
            else:
                why = "Steps must be Python (.py) files. Rewrite it as a .py file, or move it out of the recipe folder."
            raise RecipeRefused(f"{rel} is named like a step but is not a .py file. {why}", rule=3, path=rel)
        match = STEP_FILE.fullmatch(filename)
        if match:
            candidates.append((match.group(1), match.group(2), filename))
        elif (why := _near_miss(filename)) is not None:
            warn(f"{rel} is not run: {why} Rename it if it is meant to be a step.", collected)

    if not candidates:
        raise RecipeRefused(
            f"{RECIPES}/{name}/ has no step file. Add at least one, named like 01_first_step.py, "
            "starting with a # comment that says what it does.",
            rule=2,
            path=f"{RECIPES}/{name}/",
        )

    by_number: dict[str, str] = {}
    for number, _, filename in candidates:
        if number in by_number:
            raise RecipeRefused(
                f"Two steps in {RECIPES}/{name}/ share the number {number}: {by_number[number]} and {filename}. "
                "Renumber one so each step has its own number.",
                rule=2,
                path=f"{RECIPES}/{name}/{filename}",
            )
        by_number[number] = filename

    steps = []
    for number, step_name, filename in sorted(candidates):
        rel = f"{RECIPES}/{name}/{filename}"
        text = read_text(folder / filename, rel, rule=2)
        description = step_description(text)
        if not description:
            hint = ""
            if text.lstrip().startswith(('"""', "'''")):
                hint = " It starts with a docstring; useful-views reads # comment lines instead."
            raise RecipeRefused(
                f"{rel} has no top comment.{hint} Start the file with a # comment saying what the step does, "
                "for example: # Keep only the rows for 1990 onwards.",
                rule=2,
                path=rel,
            )
        if description.startswith(TODO):
            raise RecipeRefused(
                f"{rel}: its top comment still starts with TODO. Replace it with a sentence saying what the step does.",
                rule=2,
                path=rel,
            )
        steps.append(Step(name=step_name, path=rel, description=description))
    return steps


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
    description, inputs, outputs, found = check_declaration(data, rel)
    for message in found:
        warn(message, collected)
    steps = find_steps(root, name, collected)
    return Recipe(
        name=name,
        description=description,
        path=f"{RECIPES}/{name}",
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        steps=tuple(steps),
        warnings=tuple(collected),
    )

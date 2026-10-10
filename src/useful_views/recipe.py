"""Reading a recipe folder: recipe.toml, its step files and their descriptions.

This module only reads. It checks the recipe's own format (rules 1 to 3 in
SPEC.md section 10, with the details settled in section 17).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RecipeRefused, warn
from .paths import RECIPES, is_file, listdir

RECIPE_NAME = re.compile(r"^[a-z0-9_]+$")
STEP_FILE = re.compile(r"^([0-9]{2})_(.+)\.py$")
OTHER_LANGUAGE_STEP = re.compile(r"^[0-9]{2}_.+\.(R|r|ipynb|Rmd|qmd|jl|do)$")
NEAR_MISS_STEP = re.compile(r"^[0-9]+[_-].*\.py$")
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


def step_description(text: str) -> str:
    """The top comment block of a step file, joined into one line.

    Leading blank lines and machine lines (shebang, coding line, notebook cell
    markers, a Jupytext header) are skipped first.
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
        elif line == "# ---":
            i += 1
            while i < n and lines[i].strip() != "# ---":
                i += 1
            i += 1
        else:
            break
    parts = []
    while i < n and lines[i].lstrip().startswith("#"):
        line = lines[i].strip()
        if re.match(r"^#\s*(%%|In\[.*\]:)", line) or line == "# ---":
            break  # a notebook cell marker ends the description
        after_shebang = i > 0 and lines[i - 1].strip().startswith("#!")
        if line.startswith("#!") or (after_shebang and re.match(r"^#.*coding[:=]\s*[-\w.]+", line)):
            i += 1
            continue  # a shebang (and its coding line) is not part of the description
        parts.append(re.sub(r"^[#\s]+", "", line))
        i += 1
    return collapse(" ".join(p for p in parts if p))


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
        elif NEAR_MISS_STEP.fullmatch(filename):
            warn(
                f"{rel} is not run: step files are named with exactly two digits and an underscore, "
                "like 01_clean.py. Rename it if it is meant to be a step.",
                collected,
            )

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


def resolve_name(root: Path, recipe: str) -> str:
    """Accept 'name', 'recipes/name' or 'recipes/name/'; require exact case."""
    name = recipe.replace("\\", "/").strip()
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
    if not RECIPE_NAME.fullmatch(name):
        raise RecipeRefused(
            f"The recipe folder name {name!r} is not allowed: use only lowercase letters, digits and "
            f"underscores. Rename {RECIPES}/{name}/.",
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

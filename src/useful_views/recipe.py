"""Reading a recipe folder: recipe.toml, with the steps it lists, and the step files.

This module only reads. It checks the recipe's own format (rules 1 to 3
under "Rules" in the README).
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .errors import RecipeRefused, warn
from .paths import IGNORED_NAMES, RECIPES, UNSEEN, WINDOWS_RESERVED, is_file, listdir, name_key, nfc

RECIPE_NAME = re.compile(r"^[a-z0-9_]+$")
STEP_FILE = re.compile(r"^([0-9]{2})_(.+)\.py$")
OTHER_LANGUAGE_STEP = re.compile(r"^[0-9]{2}_.+\.(r|ipynb|rmd|qmd|jl|do)$", re.IGNORECASE)
STEP_FORM = "step files are named with exactly two digits, an underscore and a name ending in .py, like 01_clean.py"
KNOWN_KEYS = ("description", "inputs", "outputs", "steps")
STEP_KEYS = ("file", "description")
# Keys of recipe.toml itself that, written below a [[steps]] header, land in that step.
TOP_LISTS = ("inputs", "outputs")
STEPS_HEADER = re.compile(r"^\s*\[\[\s*steps\s*\]\]")
SINGLE_STEPS_HEADER = re.compile(r"^\s*\[\s*steps\s*\]\s*(#.*)?$")
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


def _scan(lines: list[str], upto: int) -> tuple[str | None, int, bool, int | None]:
    """Walk recipe.toml's lines up to `upto`. Returns the key whose value
    holds line `upto` (the last `key =` line that starts outside triple-quoted
    text), that key's line, whether a [[steps]] header comes before it, and
    the line where triple-quoted text still open after `upto` began (or None)."""
    key, key_at, in_steps, seen_steps = None, upto, False, False
    delim, opened = None, None
    for i, line in enumerate(lines[: upto + 1]):
        if delim is None:
            if STEPS_HEADER.match(line):
                seen_steps = True
            elif found := KEY_LINE.match(line):
                key, key_at, in_steps = found.group(1), i, seen_steps
        at = 0
        while True:
            if delim is None:
                hits = [(line.find(d, at), d) for d in ('"""', "'''") if line.find(d, at) >= 0]
                if not hits:
                    break
                pos, delim = min(hits)
                opened, at = i, pos + 3
            else:
                pos = line.find(delim, at)
                if pos < 0:
                    break
                delim, at = None, pos + 3
    return key, key_at, in_steps, opened if delim else None


def _toml_hint(text: str, error: str) -> str:
    """Advice for the TOML mistakes most often made in recipe.toml, judged
    from the key whose value the error is in and whether that key sits below
    a [[steps]] header."""
    lines = text.split("\n")
    match = ERROR_LINE.search(error)
    at = int(match.group(1)) - 1 if match else len(lines) - 1
    at = min(max(at, 0), len(lines) - 1)
    unclosed = _scan(lines, len(lines) - 1)[3]
    if unclosed is not None:
        return (f" The text in triple quotes that starts on line {unclosed + 1} is never closed: end it with the "
                "same three quotes.")
    if ("Cannot overwrite" in error or "Cannot declare" in error) and any(map(SINGLE_STEPS_HEADER.match, lines)):
        return (" Each step starts with [[steps]], with two pairs of square brackets; [steps], with one, can appear "
                "only once. Write [[steps]] above each step's file and description.")
    key, key_at, in_steps, _ = _scan(lines, at)
    if "Cannot overwrite a value" in error and key:
        if in_steps and key in STEP_KEYS:
            hint = (f" Every line below a [[steps]] header belongs to that step, so this is a second `{key}` in one "
                    f"step. If line {key_at + 1} begins another step, put [[steps]] above that step's file and "
                    "description.")
            if key == "description":
                hint += " If it is the recipe's description, move it above the first [[steps]]."
            return hint
        return f" `{key}` is set twice: keep one."
    if key == "description" and "Illegal character '\\n'" in error:
        return ' A description over several lines needs triple quotes: description = """...""".'
    if key == "description" and "escape" in error.lower():
        return (" In a description, a backslash starts an escape sequence: write it as \\\\, or put the text in "
                "triple single quotes, description = '''...''', which keep backslashes as they are.")
    if key == "description" and '"""' not in lines[key_at] and lines[at].count('"') > 2:
        return ' A description with double quotes in it needs triple quotes: description = """...""".'
    if "\\" in text and "escape" in error.lower():
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


def _step_tables(data: dict) -> list[dict]:
    """The tables under `steps`: one for [steps] or steps = {...}, else each table in the list."""
    value = data.get("steps")
    if isinstance(value, dict):
        return [value]
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def declared_paths(data: dict, key: str) -> list[str]:
    """The string paths under `key`, ignoring anything malformed. For reading
    other recipes, where malformed entries are not this recipe's problem. A
    list written below a [[steps]] header, and so read as part of a step,
    counts too: that recipe is refused for it, but what it declares is clear."""
    found = [data.get(key)] + [table.get(key) for table in _step_tables(data)]
    return list(dict.fromkeys(p for value in found if isinstance(value, list) for p in value if isinstance(p, str)))


def _steps_example(on_disk: Sequence[str]) -> str:
    """How to list the steps, with a [[steps]] block ready to paste for each step file."""
    blocks = "\n\n".join(
        f"      [[steps]]\n      file = {json.dumps(file, ensure_ascii=False)}\n      description = \"{STEP_TODO}\""
        for file in (on_disk or ["01_first_step.py"])
    )
    return (
        "list each step file at the end of recipe.toml, in the order they run, and replace each TODO with "
        f"one sentence saying what that step does:\n\n{blocks}"
    )


def _file_problem(file: object) -> str | None:
    """Why a step's `file` value is not a bare file name, or None."""
    if not isinstance(file, str) or not file.strip():
        return "`file` must be the step file's name in double quotes, like file = \"01_clean.py\""
    if "/" in file or "\\" in file:
        bare = file.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "NN_name.py"
        return f"`file` is the name of a file in this recipe's folder, not a path: write file = {json.dumps(bare)}"
    if unseen := re.search(f"[{UNSEEN}]", file):
        return (f"`file` contains an invisible character (U+{ord(unseen.group()):04X}): write the step file's "
                "name exactly")
    if file != file.strip():
        return f"`file` has spaces at the start or end: write file = {json.dumps(file.strip(), ensure_ascii=False)}"
    return None


def _check_steps(data: dict, on_disk: Callable[[], Sequence[str]], problems: list[str],
                 unknown: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], set[str]]:
    """Rule 1 for `steps`: a [[steps]] block for each step, each with a file
    name and a description. Appends to `problems`, and each unknown key to
    `unknown` as (block, key). Returns the (file, description) pairs, and the
    top-level lists found inside a step (reported here, not as missing)."""
    value = data.get("steps")
    misplaced = {key for table in _step_tables(data) for key in TOP_LISTS if key in table}
    for key in sorted(misplaced):
        problems.append(f"`{key}` is inside a [[steps]] block: every line below a [[steps]] header belongs to that "
                        f"step. Move `{key}` above the first [[steps]]")
    if "steps" not in data:
        problems.append("`steps` is missing: " + _steps_example(on_disk()))
        return [], misplaced
    if isinstance(value, dict):
        problems.append("`steps` is a single table ([steps], with one pair of square brackets, or steps = {...}): "
                        "write [[steps]], with two, above each step's file and description")
        return [], misplaced
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        problems.append('`steps` must be a [[steps]] block for each step, each with file = "01_name.py" and '
                        'description = "One sentence saying what this step does."')
        return [], misplaced
    if not value:
        problems.append("`steps` is empty: " + _steps_example(on_disk()))
        return [], misplaced
    steps: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, entry in enumerate(value, 1):
        file = entry.get("file")
        problem = _file_problem(file) if "file" in entry else None
        block = f"the [[steps]] block for {file}" if "file" in entry and problem is None else f"[[steps]] block {number}"
        if "file" not in entry:
            problems.append(f'{block} has no `file`: add file = "NN_name.py", the name of the step file')
        elif problem:
            problems.append(f"{block}: {problem}")
        elif file in seen:
            problems.append(f"`steps` lists {file!r} twice: keep one")
        else:
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
        if "file" in entry and problem is None and isinstance(text, str):
            steps.append((file, collapse(text)))
    return steps, misplaced


def check_declaration(data: dict, rel: str, on_disk: Callable[[], Sequence[str]] = tuple
                      ) -> tuple[str, list[str], list[str], list[tuple[str, str]], list[str]]:
    """Apply rule 1 to a parsed recipe.toml. Returns description, inputs,
    outputs, the steps as (file, description) pairs, and warnings. All rule 1
    problems are reported together. `on_disk` gives the files in the folder
    named like steps; it is called only for advice when `steps` is missing
    or empty."""
    problems: list[str] = []
    found: list[str] = []
    step_problems: list[str] = []
    step_unknown: list[tuple[str, str]] = []
    steps, misplaced = _check_steps(data, on_disk, step_problems, step_unknown)

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
    problems.extend(step_problems)

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
    files, no two share a number, they are exactly the files recipe.toml
    lists, in the order of their numbers, and each can be read as UTF-8.
    `listed` holds the (file, description) pairs from recipe.toml. A listed
    name that differs from the name on disk only in how accented letters are
    encoded is the same file; the step takes the name on disk."""
    folder = root / RECIPES / name
    toml = f"{RECIPES}/{name}/recipe.toml"
    listed_files = [file for file, _ in listed]
    listed_nfc = {nfc(file) for file in listed_files}
    entries = listdir(folder)
    files: list[str] = []
    candidates: list[tuple[str, str, str]] = []
    for filename in entries:
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
        elif nfc(filename) not in listed_nfc and (why := _near_miss(filename)) is not None:
            warn(f"{rel} is not run: {why} Rename it, and list it in recipe.toml, if it is meant to be a step.",
                 collected)

    problems: list[tuple[str, str]] = []  # (the file to fix, what is wrong)
    by_number: dict[str, str] = {}
    for number, _, filename in sorted(candidates):
        if number in by_number:
            problems.append((f"{RECIPES}/{name}/{filename}",
                             f"{by_number[number]} and {filename} share the number {number}: renumber one so each "
                             "step has its own number, and change its file = in recipe.toml"))
        else:
            by_number[number] = filename

    by_nfc = {nfc(f): f for f in files}
    by_key = {name_key(f): f for f in files}
    steps_on_disk = {filename for _, _, filename in candidates}
    actual: dict[str, str] = {}  # listed name -> name on disk
    for file in listed_files:
        rel = f"{RECIPES}/{name}/{file}"
        disk = file if file in files else by_nfc.get(nfc(file))
        if disk is not None and disk in steps_on_disk:
            actual[file] = disk
        elif disk is not None:
            problems.append((f"{RECIPES}/{name}/{disk}",
                             f"{rel} is listed, but it is not named like a step: {_near_miss(disk) or STEP_FORM + '.'}"))
        elif (folder / file).is_dir() or file in entries:
            problems.append((toml, f"{rel} is listed, but it is a folder, not a step file"))
        elif (other := by_key.get(name_key(file))) is not None:
            other_rel = f"{RECIPES}/{name}/{other}"
            if STEP_FILE.fullmatch(other):
                problems.append((toml, f"{rel} is listed, but the file is named {other_rel}: make the two match, "
                                       "letter case included"))
            else:
                problems.append((other_rel, f"{rel} is listed, but the file is named {other_rel}: rename the file "
                                            f"to {file}"))
        else:
            problems.append((toml, f"{rel} is listed, but there is no such file"))
    listed_keys = {name_key(file) for file in listed_files}
    in_order = [filename for _, _, filename in sorted(candidates)]
    for filename in in_order:
        if name_key(filename) not in listed_keys:
            problems.append((toml, f"{RECIPES}/{name}/{filename} is named like a step, but recipe.toml does not "
                                   "list it: add a [[steps]] block for it, or move it out of the recipe folder if "
                                   "it is not a step"))
    if not problems and [actual[file] for file in listed_files] != in_order:
        problems.append((toml, "the steps are listed in a different order from their numbers, which is the order "
                               "they run: list them as " + ", ".join(in_order)))
    if problems:
        raise RecipeRefused(
            f"The steps listed in {toml} do not match the step files in its folder:\n"
            + "\n".join(f"  - {text}" for _, text in problems),
            rule=2,
            path=problems[0][0],
        )
    steps = []
    for file, description in listed:
        rel = f"{RECIPES}/{name}/{actual[file]}"
        read_text(folder / actual[file], rel, rule=2)  # unreadable or not UTF-8: refused before anything changes
        steps.append(Step(name=STEP_FILE.fullmatch(actual[file]).group(2), path=rel, description=description))
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
    description, inputs, outputs, listed, found = check_declaration(data, rel, lambda: step_files(root, name))
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

"""new_recipe(): write a template recipe folder once."""

from __future__ import annotations

from pathlib import Path

from .errors import RecipeRefused
from .paths import RECIPES, VIEWS, exists_exact
from .project import find_root, template
from .recipe import recipe_name_problem
from .record import write_atomic


def new_recipe(name: str, *, root: str | Path | None = None) -> Path:
    """Create `recipes/<name>/` with a template `recipe.toml` and a stub step.

    `recipe.toml` lists the stub step, `01_first_step.py`, in a `[[steps]]`
    block. Its description and the step's description say TODO, and its
    inputs and outputs are empty: until you fill them in, `run` and `sync`
    refuse the recipe (rule 1) and `check` reports it as refused. The stub
    file holds only a note.

    Args:
        name: Lowercase letters, digits and underscores, like `"pwt_panel"`,
            and not a name Windows reserves (con, aux, nul, prn, com1...) or
            `__pycache__`.
        root: The project folder. By default it is found by walking up from
            the current folder to `datapackage.json`.

    Returns:
        The new recipe folder.

    Raises:
        RecipeRefused: The name is not allowed, or the folder already exists.
        ProjectNotFound: No project was found.
    """
    project = find_root(root)
    problem = recipe_name_problem(name)
    if problem:
        raise RecipeRefused(f"{name!r} is not a valid recipe name: {problem}.", path=f"{RECIPES}/{name}/")
    folder = project / RECIPES / name
    if folder.exists() or exists_exact(project, f"{RECIPES}/{name}"):
        raise RecipeRefused(
            f"{RECIPES}/{name}/ already exists. Choose another name, or edit the files in that folder.",
            path=f"{RECIPES}/{name}/",
        )
    fill = {"{name}": name, "{view}": name.replace("_", "-"), "{views}": VIEWS}

    def render(text: str) -> str:
        for key, value in fill.items():
            text = text.replace(key, value)
        return text

    folder.mkdir(parents=True)
    write_atomic(folder / "recipe.toml", render(template("recipe.toml")))
    write_atomic(folder / "01_first_step.py", render(template("01_first_step.py")))
    return folder

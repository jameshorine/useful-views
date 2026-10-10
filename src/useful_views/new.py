"""new_recipe(): write a template recipe folder once."""

from __future__ import annotations

from pathlib import Path

from .errors import RecipeRefused
from .paths import RECIPES, VIEWS, exists_exact
from .project import find_root, template
from .recipe import RECIPE_NAME
from .record import write_atomic


def new_recipe(name: str, *, root: str | Path | None = None) -> Path:
    """Create recipes/<name>/ with a template recipe.toml and one stub step.
    Returns the new folder."""
    project = find_root(root)
    if not RECIPE_NAME.fullmatch(name):
        raise RecipeRefused(
            f"{name!r} is not a valid recipe name: use only lowercase letters, digits and underscores, "
            "like pwt_panel.",
            path=f"{RECIPES}/{name}/",
        )
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

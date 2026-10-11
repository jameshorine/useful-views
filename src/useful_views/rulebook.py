"""The rules a recipe must follow, by number.

`RecipeRefused.rule` refers to these numbers, `useful-views rules` prints
them, and the README lists them under "Rules".
"""

from __future__ import annotations

from typing import NamedTuple


class Rule(NamedTuple):
    """One rule a recipe must follow.

    Attributes:
        number: The rule's number, as in `RecipeRefused.rule`.
        title: A one-line title, as shown in refusal messages.
        detail: What the rule requires, in full.
    """

    number: int
    title: str
    detail: str


RULES: dict[int, Rule] = {
    rule.number: rule
    for rule in (
        Rule(
            1,
            "recipe.toml is complete",
            "recipe.toml exists and can be read. It has a one-sentence description that does not start with "
            "TODO, and inputs and outputs, each a list of at least one path with no path listed twice.",
        ),
        Rule(
            2,
            "Steps are numbered and described",
            "The recipe has at least one step file named NN_name.py. Each starts with a # comment saying what "
            "it does, not starting with TODO, and no two steps share a number.",
        ),
        Rule(
            3,
            "Steps are Python files",
            "No file in the recipe folder is named like a step in another language (.R, .Rmd, .ipynb, .qmd, "
            ".jl, .do).",
        ),
        Rule(
            4,
            "Inputs exist and are allowed",
            "Each input is a file under raw/ or directly inside useful-views/, written relative to the project "
            "root with forward slashes, and exists with exactly that letter case. A raw input is not empty and "
            "can be read.",
        ),
        Rule(
            5,
            "Input views come from a recipe that has run",
            "An input inside useful-views/ is an output of another recipe, and that recipe has run "
            "successfully.",
        ),
        Rule(
            6,
            "No cycles",
            "The recipe does not read its own view, directly or through a chain of other recipes.",
        ),
        Rule(
            7,
            "Outputs are allowed and unique",
            "Each output is a .parquet file directly inside useful-views/, is not useful-views/README.md, and "
            "is not made by another recipe.",
        ),
        Rule(
            8,
            "Resource names are unique",
            "No two recorded files share a resource name: the file name without its extension, lowercased, "
            "with characters other than a-z, 0-9, '.', '_' and '-' turned into '-'.",
        ),
    )
}


def describe_rules() -> str:
    """All rules as plain text, one paragraph each."""
    return "\n\n".join(f"Rule {r.number}: {r.title}.\n  {r.detail}" for r in RULES.values())

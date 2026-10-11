"""Exceptions and warnings raised by useful-views.

Every message says what to fix and in which file, because the reader may be a
student or an AI tool acting for one. Every exception also carries the same
facts as attributes, and `to_dict()` returns them for scripts and tools.
"""

from __future__ import annotations

import warnings


class UsefulViewsError(Exception):
    """Base class for every error useful-views raises.

    Attributes:
        recipe: The recipe the error is about, when there is one.
        sync_result: When the error stopped `sync`, a `SyncResult` saying
            what sync had already done (those changes are kept); else None.
    """

    def __init__(self, message: str, *, recipe: str | None = None):
        super().__init__(message)
        self.recipe = recipe
        self.sync_result = None

    @property
    def message(self) -> str:
        return str(self)

    def to_dict(self) -> dict:
        """The error as plain data: its type, message and attributes."""
        return {
            "type": type(self).__name__,
            "message": str(self),
            "recipe": self.recipe,
            "sync_result": self.sync_result.to_dict() if self.sync_result is not None else None,
        }


class ProjectError(UsefulViewsError):
    """The project itself is missing or damaged, or a file could not be written."""


class ProjectNotFound(ProjectError):
    """No useful-views project was found from the current folder or `root=`."""


class RecipeRefused(UsefulViewsError):
    """A recipe was refused because it breaks a rule; nothing on disk changed
    because of it.

    Attributes:
        rule: The number of the broken rule (see `useful_views.RULES` or
            `useful-views rules`), or None when the recipe name itself is
            refused.
        rule_title: The rule's one-line title, or None.
        path: The file to fix, relative to the project root, when there is one.
        recipe: The recipe that was refused.
        refusals: Every refusal found at once. `sync` checks all recipes
            before changing anything and raises the first, with the rest here;
            otherwise this is just `[self]`.
    """

    def __init__(self, message: str, *, rule: int | None = None, path: str | None = None,
                 recipe: str | None = None):
        from .rulebook import RULES

        self.detail = message
        self.rule = rule
        self.rule_title = RULES[rule].title if rule in RULES else None
        if self.rule_title:
            message = f"{message}\n(Rule {rule}: {self.rule_title}. To see every rule: useful-views rules)"
        super().__init__(message, recipe=recipe)
        self.path = path
        self.refusals: list[RecipeRefused] = [self]

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update(rule=self.rule, rule_title=self.rule_title, path=self.path)
        data["refusals"] = [
            {"recipe": r.recipe, "rule": r.rule, "rule_title": r.rule_title, "path": r.path, "message": r.detail}
            for r in self.refusals
        ]
        return data


class StepFailed(UsefulViewsError):
    """A step exited with an error. Nothing was recorded, and the recipe's views
    were removed; its work files are kept for inspection.

    Attributes:
        step: The step file, relative to the project root.
        returncode: The step's exit code.
        stderr: Everything the step wrote to its error output.
        recipe: The recipe the step belongs to.
    """

    def __init__(self, message: str, *, step: str, returncode: int, stderr: str, recipe: str | None = None):
        super().__init__(message, recipe=recipe)
        self.step = step
        self.returncode = returncode
        self.stderr = stderr

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update(step=self.step, returncode=self.returncode, stderr=self.stderr)
        return data


class OutputError(UsefulViewsError):
    """After the steps ran, a declared output was missing, empty or unreadable.
    Nothing was recorded, and the recipe's views were removed.

    Attributes:
        paths: The outputs that were not right.
        problems: (path, reason) for each of them.
        recipe: The recipe whose outputs these are.
    """

    def __init__(self, message: str, *, paths: list[str], problems: list[tuple[str, str]] | None = None,
                 recipe: str | None = None):
        super().__init__(message, recipe=recipe)
        self.paths = paths
        self.problems = problems or [(p, "") for p in paths]

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["paths"] = list(self.paths)
        data["problems"] = [{"path": p, "reason": r} for p, r in self.problems]
        return data


class UsefulViewsWarning(UserWarning):
    """Something worth knowing that does not stop the operation."""


warnings.simplefilter("always", UsefulViewsWarning)


def warn(message: str, collected: list[str] | None = None) -> None:
    """Issue a warning and, if given, append it to `collected`."""
    if collected is not None:
        collected.append(message)
    warnings.warn(message, UsefulViewsWarning, stacklevel=3)

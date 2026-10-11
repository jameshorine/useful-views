"""Exceptions and warnings raised by useful-views.

Every message says what to fix and in which file, because the reader may be a
student or an AI tool acting for one.
"""

from __future__ import annotations

import warnings


class UsefulViewsError(Exception):
    """Base class for every error useful-views raises."""


class ProjectError(UsefulViewsError):
    """The project itself is missing or damaged."""


class ProjectNotFound(ProjectError):
    """No useful-views project was found."""


class RecipeRefused(UsefulViewsError):
    """A recipe was refused before anything on disk changed.

    `rule` is the number of the rule listed under "Rules" in the README, or
    None when the recipe name itself is refused. `path` is the file to fix,
    relative to the project root, when there is one.
    """

    def __init__(self, message: str, *, rule: int | None = None, path: str | None = None):
        super().__init__(message)
        self.rule = rule
        self.path = path


class StepFailed(UsefulViewsError):
    """A step exited with an error. Nothing was recorded."""

    def __init__(self, message: str, *, step: str, returncode: int, stderr: str):
        super().__init__(message)
        self.step = step
        self.returncode = returncode
        self.stderr = stderr


class OutputError(UsefulViewsError):
    """After the steps ran, a declared output was missing, empty or unreadable."""

    def __init__(self, message: str, *, paths: list[str]):
        super().__init__(message)
        self.paths = paths


class UsefulViewsWarning(UserWarning):
    """Something worth knowing that does not stop the operation."""


warnings.simplefilter("always", UsefulViewsWarning)


def warn(message: str, collected: list[str] | None = None) -> None:
    """Issue a warning and, if given, append it to `collected`."""
    if collected is not None:
        collected.append(message)
    warnings.warn(message, UsefulViewsWarning, stacklevel=3)

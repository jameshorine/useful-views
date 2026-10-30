"""useful-views: a fixed project structure in which every derived table
records the recipe that made it."""

from importlib.metadata import PackageNotFoundError, version as _version

from .status import Finding, check
from .errors import (
    OutputError,
    ProjectError,
    ProjectNotFound,
    RecipeRefused,
    StepFailed,
    UsefulViewsError,
    UsefulViewsWarning,
)
from .cookbooks import CookbookError, CookbookResult, cookbook
from .examples import ExampleError, example
from .fingerprints import DataHashError, data_hash
from .new import new_recipe
from .project import init, root
from .rulebook import RULES, Rule
from .runner import RunResult, run
from .syncing import SyncResult, sync

try:
    __version__ = _version("useful-views")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "CookbookError",
    "CookbookResult",
    "DataHashError",
    "ExampleError",
    "Finding",
    "OutputError",
    "ProjectError",
    "ProjectNotFound",
    "RULES",
    "RecipeRefused",
    "Rule",
    "RunResult",
    "StepFailed",
    "SyncResult",
    "UsefulViewsError",
    "UsefulViewsWarning",
    "check",
    "cookbook",
    "data_hash",
    "example",
    "init",
    "new_recipe",
    "root",
    "run",
    "sync",
]

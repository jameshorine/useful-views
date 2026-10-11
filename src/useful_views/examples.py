"""example(): download a ready-to-run example project.

Example data lives in the useful-views-data repository, the way seaborn's
lives in seaborn-data. Each release of this package pins one tag of that
repository and the SHA-256 of every file it downloads, so an example never
changes under a user and a damaged download is never used.
"""

from __future__ import annotations

import hashlib
import os
import urllib.error
import urllib.request
from pathlib import Path

from .errors import UsefulViewsError
from .project import check_not_nested, init

DATA_REPO = "https://raw.githubusercontent.com/jameshorine/useful-views-data"
DATA_TAG = "examples-v1"
# Set this to download from somewhere else: a mirror, or a local copy as a
# file:// URL laid out like the repository (examples/<name>/...).
DATA_URL_ENV = "USEFUL_VIEWS_DATA_URL"
ISSUES = "https://github.com/jameshorine/useful-views/issues"

EXAMPLES = {
    "pwt": {
        "description": "Penn World Table 11.0 extract for four countries, with two recipes.",
        "files": {
            "README.md": "3e20d84874544ac588bf0fcd72e5fd84a6e03a88a10c896b81f1c7c7c44a4c13",
            "raw/pwt110_bra_deu_esp_usa.csv": "e7d172ce2594e4c2717aa9efd2558b2868745f5b1124861548d913ade61476eb",
            "recipes/pwt_panel/recipe.toml": "d8b119db27cfb18dc48fff42bd09c467687ff8b72d4590694f994173a3879478",
            "recipes/pwt_panel/01_reshape_long.py": "29e51ff4afd23ebdcd320ec0880855cac7c01ec8b3bc647bd2b2546e83da629a",
            "recipes/pwt_panel/02_keep_core_variables.py": "10033b28165644b38bd6c3368867ba31f6cd86408603762e1252710388cc80fa",
            "recipes/pwt_panel/03_pivot_to_panel.py": "258dfe35cb806c52d8bbcbc57399463d2c50d17cbd8ffd99302dea2a266da84e",
            "recipes/gdp_per_capita/recipe.toml": "f650e3a4953413302d89cf57ebb1086b8ef0777e1ae77eae19d7f3c7bb0240e5",
            "recipes/gdp_per_capita/01_derive_measures.py": "5e7da67ec7db114f24a0913974b435815998b924a3a1d5b6dea3c0ba02695523",
        },
    },
}


class ExampleError(UsefulViewsError):
    """An example could not be downloaded or set up."""


def _base_url() -> str:
    return (os.environ.get(DATA_URL_ENV) or f"{DATA_REPO}/{DATA_TAG}").rstrip("/")


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise ExampleError(
            f"Could not download {url} ({reason}). Check your internet connection and try again."
        ) from exc


def example(name: str, path: str | Path | None = None) -> Path:
    """Download a ready-to-run example project.

    The example's raw data and recipes come from the useful-views-data
    repository, at the version this release of useful-views pins, and each
    file is checked against its SHA-256 before anything is written. The
    folder is then set up like `init` does. Make its views with
    `useful-views sync`.

    Args:
        name: The example, such as `"pwt"` (Penn World Table extract).
        path: The folder to create; default `"<name>-example"`. It must not
            exist, or be empty.

    Returns:
        The project folder, as an absolute path.

    Raises:
        ExampleError: The name is unknown, the folder is not empty, a file
            could not be downloaded, or a checksum did not match. Nothing is
            written in those cases.
        ProjectError: The folder is inside another project.
    """
    if name not in EXAMPLES:
        listing = "; ".join(f"{n}: {e['description']}" for n, e in EXAMPLES.items())
        raise ExampleError(f"There is no example named {name!r}. Available examples: {listing}")
    folder = Path(path if path is not None else f"{name}-example").expanduser().resolve()
    if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
        raise ExampleError(f"{folder} already exists and is not an empty folder. Choose another path.")
    check_not_nested(folder)

    base = f"{_base_url()}/examples/{name}"
    files: dict[str, bytes] = {}
    for rel, expected in EXAMPLES[name]["files"].items():
        url = f"{base}/{rel}"
        data = _download(url)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ExampleError(
                f"{url} is not the file this version of useful-views expects (its checksum differs), "
                f"so nothing was written. Please report it at {ISSUES}"
            )
        files[rel] = data

    for rel, data in files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return init(folder)

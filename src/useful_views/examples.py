"""example(): download a ready-to-run example project.

Example data lives in the useful-views-data repository, the way seaborn's
lives in seaborn-data. Each release of this package pins one tag of that
repository and the SHA-256 of every file it downloads, so an example never
changes under a user and a damaged download is never used.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .errors import UsefulViewsError
from .paths import is_ignored
from .project import check_not_nested, init, too_broad

DATA_REPO = "https://raw.githubusercontent.com/jameshorine/useful-views-data"
DATA_TAG = "examples-v2"
# Set this to download from somewhere else: a mirror, or a local copy (a
# folder path or file:// URL) laid out like the repository (examples/<name>/...).
DATA_URL_ENV = "USEFUL_VIEWS_DATA_URL"
ISSUES = "https://github.com/jameshorine/useful-views/issues"

EXAMPLES = {
    "pwt": {
        "description": "Penn World Table 11.0 extract for four countries, with two recipes.",
        "files": {
            "README.md": "7911d2ef0326d7f31f603a56bcefd571a8f1290c24e91085a609d3293cccb830",
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
    """An example could not be downloaded or set up.

    Attributes:
        kind: What went wrong, for scripts: "unknown-example", "folder-not-empty",
            "network", "tls" (the secure connection could not be verified),
            "not-found" or "checksum".
    """

    def __init__(self, message: str, *, kind: str | None = None):
        super().__init__(message)
        self.kind = kind

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["kind"] = self.kind
        return data


def _mirror() -> str | Path | None:
    """The source set with USEFUL_VIEWS_DATA_URL: a URL, or a local folder
    (a path, or a file: URL), which is read directly from disk."""
    value = os.environ.get(DATA_URL_ENV, "").strip()
    if not value:
        return None
    if re.match(r"^[A-Za-z]:[\\/]", value) or not re.match(r"^[A-Za-z][A-Za-z0-9+.-]+:", value):
        return Path(value).expanduser().resolve()  # a path: /srv/mirror, C:\mirror, \\server\share
    if value.lower().startswith("file:"):
        parts = urlsplit(value)
        where = f"//{parts.netloc}{parts.path}" if parts.netloc not in ("", "localhost") else parts.path
        return Path(urllib.request.url2pathname(where)).resolve()
    if urlsplit(value).username is not None or "@" in urlsplit(value).netloc:
        raise ExampleError(
            f"{DATA_URL_ENV} contains a user name or password, which useful-views does not send and does not "
            "show. Use a mirror that needs no login, or a local copy of the files (a folder path).",
            kind="network",
        )
    return value.rstrip("/")


def _source() -> str | Path:
    return _mirror() or f"{DATA_REPO}/{DATA_TAG}"


def _shown(url: str) -> str:
    """A URL without any user name or password in it, for messages."""
    return re.sub(r"//[^/@\s]*@", "//", url)


def _advice(kind: str) -> str:
    mirror = _mirror()
    if mirror and kind in ("not-found", "checksum"):
        return (
            f"{DATA_URL_ENV} is set to {_shown(str(mirror))}, so the examples come from there. Check that it holds "
            f"a copy of the useful-views-data repository at {DATA_TAG} (examples/<name>/...), or unset it to "
            "download from GitHub."
        )
    offline = (
        f"Without internet access, copy the useful-views-data repository at {DATA_TAG} some other way and set "
        f"{DATA_URL_ENV} to that folder."
    )
    if kind == "tls":
        return (
            "The secure connection could not be verified. This happens on networks that inspect encrypted "
            "traffic, and with a Python that has no certificates installed (on macOS, run 'Install "
            "Certificates.command' in the Python folder). Ask your IT team for the certificate and set "
            f"SSL_CERT_FILE to it. {offline}"
        )
    if kind == "not-found":
        return f"The file is not where this version of useful-views expects it. Please report it at {ISSUES}"
    return (
        "Check your internet connection and try again. Behind a proxy, set HTTPS_PROXY to its address. "
        + offline
    )


def _download(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        cause = exc
        kind, reason = ("not-found" if exc.code == 404 else "network"), f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        cause = exc
        reason = exc.reason
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            kind = "tls"
        else:
            kind = "network"
    except (http.client.HTTPException, OSError, ValueError) as exc:
        cause = exc
        kind, reason = "network", f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    reason = re.sub(r"[^\s'\"/@]+@", "", str(reason))  # never show a password, wherever urllib put it
    raise ExampleError(f"Could not download {_shown(url)} ({reason}). {_advice(kind)}", kind=kind) from cause


def _read(source: str | Path, rel: str) -> tuple[str, bytes]:
    """Where a file of the example is, for messages, and its bytes."""
    if isinstance(source, Path):
        path = source / rel
        try:
            return str(path), path.read_bytes()
        except OSError as exc:
            kind = "not-found" if isinstance(exc, (FileNotFoundError, IsADirectoryError)) else "network"
            raise ExampleError(f"Could not read {path} ({exc.strerror or exc}). {_advice(kind)}", kind=kind) from exc
    url = f"{source}/{rel}"
    return url, _download(url)


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
        ExampleError: The name is unknown, the folder is not empty (or is
            your home folder or the top of a drive), a file could not be
            downloaded, or a checksum did not match. Nothing is written in
            those cases.
        ProjectError: The folder is inside another project.
    """
    if name not in EXAMPLES:
        listing = "; ".join(f"{n}: {e['description']}" for n, e in EXAMPLES.items())
        raise ExampleError(f"There is no example named {name!r}. Available examples: {listing}",
                           kind="unknown-example")
    folder = Path(path if path is not None else f"{name}-example").expanduser().resolve()
    # Hidden files (.DS_Store, Thumbs.db) do not count: the folder looks empty.
    if folder.exists() and (not folder.is_dir() or any(not is_ignored(n) for n in os.listdir(folder))):
        raise ExampleError(f"{folder} already exists and is not an empty folder. Choose another path.",
                           kind="folder-not-empty")
    why = too_broad(folder)
    if why:
        raise ExampleError(f"{folder} {why}; make the example in a folder of its own, such as: useful-views example "
                           f"{name} (it creates {name}-example/).", kind="folder-not-empty")
    check_not_nested(folder)

    source = _source()
    files: dict[str, bytes] = {}
    for rel, expected in EXAMPLES[name]["files"].items():
        url, data = _read(source, f"examples/{name}/{rel}")
        if hashlib.sha256(data).hexdigest() != expected:
            if _mirror():
                why = (
                    f"{DATA_URL_ENV} points to a copy that is not the {DATA_TAG} version, or whose files were "
                    "changed (git on Windows can change line endings: check it out with core.autocrlf=false)."
                )
            else:
                why = f"Please report it at {ISSUES}"
            raise ExampleError(
                f"{_shown(url)} is not the file this version of useful-views expects (its checksum differs), "
                f"so nothing was written. {why}",
                kind="checksum",
            )
        files[rel] = data

    for rel, data in files.items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return init(folder)

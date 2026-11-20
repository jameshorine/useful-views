"""sources.toml: where each raw file comes from.

A project may describe its raw data in `sources.toml` at its root, one
`[[sources]]` block per source: its title, the raw files it covers, where and
how to get it, its license and its citation, and whether the files are
shared in the replication package. useful-views copies these details onto
each raw file's record in `datapackage.json` (Data Package `sources` and
`licenses`), so tools and the generated catalog and cookbook can show them.
They are metadata: changing them never marks a recipe out of date.
"""

from __future__ import annotations

import datetime
import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ProjectError
from .paths import RAW, UNSEEN, path_problem

SOURCES = "sources.toml"
# The text fields a source may have, in the order they are recorded.
TEXT_FIELDS = ("url", "doi", "access", "cost", "time", "license", "license_url", "citation", "reason")
KNOWN = ("title", "files", *TEXT_FIELDS, "obtained", "shared")
# What a raw resource in datapackage.json gets from sources.toml.
RESOURCE_FIELDS = ("sources", "licenses")
# A Data Package license name: an identifier such as CC-BY-4.0 or ODC-BY-1.0.
LICENSE_NAME = re.compile(r"^[-a-zA-Z0-9._]+$")


class SourcesError(ProjectError):
    """sources.toml cannot be read, or does not have the shape it needs.

    Attributes:
        path: Always "sources.toml".
    """

    path = SOURCES


@dataclass(frozen=True)
class Source:
    """One `[[sources]]` block of sources.toml."""

    title: str
    files: tuple[str, ...]
    details: dict = field(default_factory=dict)  # the optional fields given, by name

    def recorded(self) -> dict:
        """What each of its raw files gets in datapackage.json: `sources`, and
        `licenses` when the license can be written as a Data Package license."""
        entry: dict = {"title": self.title}
        url = self.details.get("url") or (f"https://doi.org/{self.details['doi']}" if "doi" in self.details else None)
        if url:
            entry["path"] = url
        license = _license(self.details)
        for key in ("doi", "access", "cost", "time", "obtained", "citation", "shared", "reason"):
            if key in self.details:
                entry[key] = self.details[key]
        if license is None and "license" in self.details:
            entry["license"] = self.details["license"]
        fields = {"sources": [entry]}
        if license is not None:
            fields["licenses"] = [license]
        return fields


def _license(details: dict) -> dict | None:
    """A Data Package license object: it needs a `name` (an identifier) or a
    `path` (a URL). None when neither can be given."""
    text, url = details.get("license"), details.get("license_url")
    if text is None and url is None:
        return None
    license = {}
    if text is not None:
        license["name" if LICENSE_NAME.fullmatch(text) else "title"] = text
    if url is not None:
        license["path"] = url
    return license if "name" in license or "path" in license else None


@dataclass(frozen=True)
class SourcesState:
    """sources.toml as run, sync and check see it.

    Attributes:
        sources: Its sources, in file order; None when there is no
            sources.toml, or it cannot be read.
        error: Why it cannot be read, or None.
        warnings: Notes about it, such as keys useful-views does not read.
    """

    sources: tuple[Source, ...] | None = None
    error: str | None = None
    warnings: tuple[str, ...] = ()

    def by_file(self) -> dict[str, Source]:
        return {rel: source for source in self.sources or () for rel in source.files}


def _text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SourcesError(f"Cannot read {SOURCES}: {exc.strerror or exc}. Check its permissions.") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourcesError(
            f"{SOURCES} is not saved as UTF-8. Open it in your editor and save it with UTF-8 encoding."
        ) from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _syntax_hint(text: str, error: str) -> str:
    """Advice for the TOML mistakes most likely in sources.toml, where the
    long values (access, citation) often hold quotes and backslashes."""
    from .recipe import ERROR_LINE, _scan

    lines = text.split("\n")
    unclosed = _scan(lines, len(lines) - 1)[3]
    if unclosed is not None:
        return (f" The text in triple quotes that starts on line {unclosed + 1} is never closed: end it with the "
                "same three quotes.")
    if "escape" in error.lower():
        return (" A backslash starts an escape sequence: write it as \\\\, or put the text in triple single "
                "quotes, '''...''', which keep it as written.")
    match = ERROR_LINE.search(error)
    at = min(max(int(match.group(1)) - 1, 0), len(lines) - 1) if match else None
    if at is not None and lines[at].count('"') > 2 and '"""' not in lines[at]:
        return ' Text with double quotes in it goes in triple quotes: """...""".'
    if "Illegal character '\\n'" in error:
        return ' Text over several lines goes in triple quotes: """...""".'
    if "Cannot overwrite" in error or "Cannot declare" in error:
        return " Each source starts with its own [[sources]] header, with two pairs of square brackets."
    return ""


def read_sources(root: Path) -> tuple[list[Source], list[str]] | None:
    """Read and check sources.toml. Returns its sources and any notes, or
    None when the project has no sources.toml. Raises SourcesError, listing
    every problem, when it cannot be read or has the wrong shape."""
    path = root / SOURCES
    if not path.is_file():
        if path.exists():
            raise SourcesError(f"{SOURCES} is not a file. Delete it, or replace it with a file.")
        return None
    text = _text(path)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise SourcesError(f"{SOURCES} is not valid TOML: {exc}. Fix the syntax in that file."
                           f"{_syntax_hint(text, str(exc))}") from exc

    problems: list[str] = []
    notes: list[str] = []
    for key in data:
        if key != "sources":
            notes.append(f"{SOURCES} has a key `{key}` useful-views does not read; it reads only [[sources]] "
                         "blocks. Check the spelling.")
    value = data.get("sources", [])
    if isinstance(value, dict):
        problems.append("`sources` is a single table ([sources]): write [[sources]], with two pairs of square "
                        "brackets, above each source")
        value = []
    elif not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        problems.append("`sources` must be a [[sources]] block for each source")
        value = []

    sources: list[Source] = []
    seen: dict[str, str] = {}
    for number, entry in enumerate(value, 1):
        title = entry.get("title")
        named = isinstance(title, str) and bool(" ".join(title.split()))
        block = f"the source {' '.join(title.split())!r}" if named else f"[[sources]] block {number}"
        if "title" not in entry:
            problems.append(f'{block} has no `title`: add title = "What the data is, as its publisher names it"')
        elif not named:
            problems.append(f"{block}: `title` must be a non-empty string")
        files = entry.get("files")
        good_files: list[str] = []
        if "files" not in entry:
            problems.append(f'{block} has no `files`: list the raw files it covers, like files = ["raw/data.csv"]')
        elif not isinstance(files, list) or not files:
            problems.append(f'{block}: `files` must be a list of at least one path, like files = ["raw/data.csv"]')
        else:
            for rel in files:
                problem = path_problem(rel)
                if problem is None and not rel.startswith(f"{RAW}/"):
                    problem = f"{rel!r} is not in {RAW}/: a source describes raw files"
                if problem is not None:
                    problems.append(f"{block}: {problem}")
                elif rel in seen:
                    where = "twice" if seen[rel] == block else f"and in {seen[rel]}"
                    problems.append(f"{block} lists {rel} {where}: list each raw file in one source only")
                else:
                    seen[rel] = block
                    good_files.append(rel)
        details: dict = {}
        for key in TEXT_FIELDS:
            if key in entry:
                if isinstance(entry[key], str) and entry[key].strip():
                    details[key] = entry[key].strip() if key in ("url", "doi", "license", "license_url") else \
                        " ".join(entry[key].split())
                else:
                    problems.append(f"{block}: `{key}` must be text in quotes, not empty")
        if "obtained" in entry:
            obtained = entry["obtained"]
            if isinstance(obtained, datetime.datetime):
                details["obtained"] = obtained.date().isoformat()
            elif isinstance(obtained, datetime.date):
                details["obtained"] = obtained.isoformat()
            elif isinstance(obtained, str) and obtained.strip():
                details["obtained"] = obtained.strip()
            else:
                problems.append(f"{block}: `obtained` must be a date, like obtained = 2026-10-09")
        if "shared" in entry:
            if isinstance(entry["shared"], bool):
                details["shared"] = entry["shared"]
            else:
                problems.append(f"{block}: `shared` must be true or false, without quotes")
        for key, text in details.items():
            unseen = re.search(f"[{UNSEEN}]", text) if isinstance(text, str) else None
            if unseen:
                problems.append(f"{block}: `{key}` contains an invisible character (U+{ord(unseen.group()):04X})")
        for key in entry:
            if key not in KNOWN:
                notes.append(f"{SOURCES}: {block} has a key `{key}` useful-views does not read (check the "
                             f"spelling: the keys are {', '.join(KNOWN)}).")
        if named and good_files:
            sources.append(Source(title=" ".join(title.split()), files=tuple(good_files), details=details))

    if problems:
        raise SourcesError(f"{SOURCES} needs fixing:\n" + "\n".join(f"  - {p}" for p in problems))
    return sources, notes


def load_sources(root: Path) -> SourcesState:
    """sources.toml for run, sync and check: never raises."""
    try:
        read = read_sources(root)
    except SourcesError as exc:
        return SourcesState(error=str(exc))
    if read is None:
        return SourcesState()
    sources, notes = read
    return SourcesState(sources=tuple(sources), warnings=tuple(notes))


def unreadable_note(state: SourcesState) -> str:
    return (f"{state.error}\nUntil it is fixed, the source details in datapackage.json are kept as they were "
            "recorded.")


def with_sources(data: dict, state: SourcesState, recorded: dict | None = None) -> dict:
    """`data` with each raw resource's `sources` and `licenses` taken from
    sources.toml. With no sources.toml, raw resources have none. When it
    cannot be read, each raw resource keeps those it had in `recorded`
    (datapackage.json as it was read), so nothing is lost."""
    if state.error is not None:
        kept = {r["path"]: {k: r[k] for k in RESOURCE_FIELDS if k in r}
                for r in (recorded or data)["resources"] if "producedBy" not in r}
    else:
        by_file = state.by_file()
        kept = {rel: source.recorded() for rel, source in by_file.items()}
    resources = []
    for resource in data["resources"]:
        if "producedBy" in resource:
            resources.append(resource)
            continue
        base = {k: v for k, v in resource.items() if k not in RESOURCE_FIELDS}
        extra = kept.get(resource["path"], {})
        head = {k: v for k, v in base.items() if k != "schema"}
        tail = {"schema": base["schema"]} if "schema" in base else {}
        resources.append({**head, **extra, **tail})
    return {**data, "resources": resources}


def undescribed(state: SourcesState, raw_paths: set[str]) -> list[str]:
    """The raw files in `raw_paths` that sources.toml does not cover. Empty
    when there is no sources.toml (describing sources is opted into) or it
    cannot be read."""
    if state.sources is None:
        return []
    covered = state.by_file()
    return sorted(rel for rel in raw_paths if rel not in covered)


def undescribed_note(paths: list[str]) -> str:
    listing = ", ".join(paths)
    return (f"{SOURCES} gives no source for {listing}, which {'a recipe reads' if len(paths) == 1 else 'recipes read'}. "
            "Add it to a [[sources]] block, so the records say where it comes from.")


def grouped(data: dict) -> list[dict]:
    """The sources recorded in datapackage.json, one per source, each with the
    raw files it covers, in the order of their first file."""
    groups: dict[str, dict] = {}
    for resource in data["resources"]:
        if "producedBy" in resource or not resource.get("sources"):
            continue
        entry = resource["sources"][0]
        key = json.dumps([entry, resource.get("licenses")], sort_keys=True, ensure_ascii=False)
        if key not in groups:
            groups[key] = {"title": entry["title"], "files": [],
                           **{k: v for k, v in entry.items() if k != "title"}}
            if "licenses" in resource:
                groups[key]["licenses"] = resource["licenses"]
        groups[key]["files"].append(resource["path"])
    return list(groups.values())

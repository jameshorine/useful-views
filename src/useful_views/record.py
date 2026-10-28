"""Describing files and writing datapackage.json and useful-views/README.md.

datapackage.json is the record store: its `recipes` list holds one record
per recorded recipe, and its `resources` list describes the raw files those
recipes read and the views they made ("Generated files" in the README).
Each view's entry also holds its fingerprint (its data hash) and, in
`producedBy`, the fingerprints of the views its recipe read.
"""

from __future__ import annotations

import codecs
import json
import os
import re
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .errors import ProjectError
from .fingerprints import DataHashError, data_hash
from .paths import CATALOG, DATAPACKAGE, VIEWS, resource_name, slug

BOM = b"\xef\xbb\xbf"


# --- describing files -------------------------------------------------------


def field_type(t: pa.DataType) -> str:
    """Map an Arrow type to a Table Schema field type."""
    if pa.types.is_dictionary(t):
        t = t.value_type
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "number"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_string(t) or pa.types.is_large_string(t) or getattr(pa.types, "is_string_view", lambda _: False)(t):
        return "string"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_timestamp(t):
        return "datetime"
    if pa.types.is_time(t):
        return "time"
    return "any"


def schema_of(arrow_schema: pa.Schema) -> dict:
    return {"fields": [{"name": f.name, "type": field_type(f.type)} for f in arrow_schema]}


def describe_parquet(root: Path, rel: str) -> dict:
    """Describe a parquet file from its own column types. Raises pyarrow errors."""
    schema = pq.read_schema(root / rel)
    return {
        "name": resource_name(rel),
        "type": "table",
        "path": rel,
        "scheme": "file",
        "format": "parquet",
        "mediatype": "application/parquet",
        "schema": schema_of(schema),
    }


def describe_file(rel: str) -> dict:
    """Describe a raw file that is not a table useful-views can read."""
    resource = {"name": resource_name(rel), "type": "file", "path": rel, "scheme": "file"}
    filename = rel.rsplit("/", 1)[-1]
    if "." in filename:
        resource["format"] = filename.rsplit(".", 1)[-1].lower()
    return resource


def scan_text(path: Path) -> tuple[bool, bool]:
    """Read a file once, in pieces: (is it UTF-8, does it contain "0x")."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    hex_like = False
    previous = b""
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                decoder.decode(chunk)
                hex_like = hex_like or b"0x" in (previous + chunk).lower()
                previous = chunk[-1:]
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False, hex_like
    return True, hex_like


# The forms Frictionless accepts for its default date, time and datetime
# formats. Values pyarrow parses in any other form (a date padded with spaces,
# the year 0000) are recorded as strings.
DEFAULT_FORMAT = {
    "date": r"^(000[1-9]|00[1-9]\d|0[1-9]\d\d|[1-9]\d{3})-\d{2}-\d{2}$",  # no year 0000 (RE2: no lookahead)
    "time": r"^\d{2}:\d{2}:\d{2}(\.\d+)?$",
    "datetime": r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$",
}
# pyarrow also reads hexadecimal (0x1F) as an integer, which Frictionless does
# not; integer columns are checked only when the file contains "0x" at all.
INTEGER_FORMAT = r"^\s*[+-]?\d+\s*$"
OTHER_DELIMITERS = (";", "\t", "|")


def describe_csv(root: Path, rel: str) -> dict:
    """Describe a CSV file, inferring column types over the whole file.

    The schema must be one the file passes under the Data Package standard
    (frictionless validate passes), so only empty cells count as missing (a column holding "NA"
    is a string column), and times and datetimes in a non-standard form are
    strings. A CSV that is not UTF-8, that pyarrow cannot parse, that has
    blank rows, that is not comma-separated, or whose header names are
    blank, repeated or padded with spaces is recorded as a plain file
    without a schema.
    """
    path = root / rel
    with open(path, "rb") as f:
        encoding = "utf-8-sig" if f.read(3) == BOM else "utf-8"
    utf8, hex_like = scan_text(path)
    if not utf8:
        return describe_file(rel)
    parse = pacsv.ParseOptions(ignore_empty_lines=False)
    convert = pacsv.ConvertOptions(null_values=[""], strings_can_be_null=True)
    try:
        table = pacsv.read_csv(path, parse_options=parse, convert_options=convert)
    except pa.ArrowException:
        return describe_file(rel)
    names = table.schema.names
    if (
        any(not n.strip() or n != n.strip() for n in names)
        or len(set(names)) != len(names)
        or (len(names) == 1 and any(d in names[0] for d in OTHER_DELIMITERS))
        or any(pa.types.is_binary(f.type) or pa.types.is_large_binary(f.type) for f in table.schema)
    ):
        return describe_file(rel)
    if table.num_rows and names:
        blank = pc.is_null(table.column(0))
        column = None
        for column in table.columns[1:]:
            blank = pc.and_(blank, pc.is_null(column))
        has_blank_row = pc.any(blank).as_py()
        del blank, column
        if has_blank_row:
            return describe_file(rel)

    fields = schema_of(table.schema)["fields"]
    del table  # free it before reading again: raw files can be large
    formats = {**DEFAULT_FORMAT, "integer": INTEGER_FORMAT} if hex_like else DEFAULT_FORMAT
    checks = {f["name"]: formats[f["type"]] for f in fields if f["type"] in formats}
    if checks:
        # Only the columns to check, read as text.
        as_text = pacsv.read_csv(
            path,
            parse_options=parse,
            convert_options=pacsv.ConvertOptions(
                null_values=[""], strings_can_be_null=True, column_types={n: pa.string() for n in checks},
                include_columns=list(checks),
            ),
        )
        for field in fields:
            pattern = checks.get(field["name"])
            if pattern is None:
                continue
            values = pc.drop_null(as_text.column(field["name"]))
            if not pc.all(pc.match_substring_regex(values, pattern)).as_py():
                field["type"] = "string"
    return {
        "name": resource_name(rel),
        "type": "table",
        "path": rel,
        "scheme": "file",
        "format": "csv",
        "mediatype": "text/csv",
        "encoding": encoding,
        "schema": {"fields": fields},
    }


def describe_raw(root: Path, rel: str) -> dict:
    """Describe a raw input. Raises pyarrow errors for an unreadable parquet."""
    lower = rel.lower()
    if lower.endswith(".csv"):
        return describe_csv(root, rel)
    if lower.endswith(".parquet"):
        return describe_parquet(root, rel)
    return describe_file(rel)


def describe_view(root: Path, rel: str, recipe: str, inputs: list[str], fingerprint: str | None,
                  input_fingerprints: dict[str, str | None]) -> dict:
    described = describe_parquet(root, rel)
    described["fingerprint"] = fingerprint
    described["producedBy"] = {
        "recipe": recipe,
        "inputs": list(inputs),
        "inputFingerprints": dict(input_fingerprints),
    }
    return view_layout(described)


# The fields that say what a view is and where it came from come first, so a
# reader sees them together; its schema, often long, comes last.
VIEW_FIRST = ("name", "path", "fingerprint", "producedBy")


def view_layout(resource: dict) -> dict:
    first = {k: resource[k] for k in VIEW_FIRST if k in resource}
    return {**first, **{k: v for k, v in resource.items() if k not in first}}


# --- fingerprints -------------------------------------------------------------

# The shape of a recorded fingerprint: the rules' name and version, the
# algorithm, and the digest (see user_guide/reference/data-hash.md).
FINGERPRINT = re.compile(r"^datahash[1-9][0-9]*:[a-z0-9]+:[0-9a-f]+$")


def fingerprint_of(path: Path) -> str | None:
    """A view's fingerprint: its data hash, or None when the rules do not
    cover one of its columns (a list or struct in a cell). Raises
    DataHashError when the file cannot be read."""
    try:
        return data_hash(path)
    except DataHashError as error:
        if error.column is None:
            raise
        return None


def known_fingerprint(root: Path, rel: str, known: dict) -> str | None:
    """fingerprint_of, hashing each version of a file once. `known` holds what
    was hashed before, by path, with the file's size, modification time and
    file id then; a file that still has all three is not hashed again."""
    st = os.stat(root / rel)
    stamp = (st.st_size, st.st_mtime_ns, st.st_ino)
    if rel in known and known[rel][0] == stamp:
        return known[rel][1]
    value = fingerprint_of(root / rel)
    known[rel] = (stamp, value)
    return value


# --- datapackage.json -------------------------------------------------------


def empty_datapackage(root: Path) -> dict:
    return {"name": slug(root.name), "resources": [], "recipes": []}


def load_datapackage(root: Path) -> dict:
    """Read datapackage.json, checking that it has the shape useful-views writes."""
    path = root / DATAPACKAGE
    restore = (
        "datapackage.json is generated by useful-views and should not be edited by hand. "
        "Restore it from git (git checkout -- datapackage.json), or, if it was never "
        "committed, delete it and run useful-views init, then run each recipe again."
    )
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProjectError(f"{path} is missing. {restore}") from exc
    except UnicodeDecodeError as exc:
        raise ProjectError(f"{path} is not valid JSON ({exc}). {restore}") from exc
    if re.search(r"^(<<<<<<<|>>>>>>>)( |$)", text, re.MULTILINE):
        generated = [rel for rel in (DATAPACKAGE, CATALOG, "cookbook.json") if (root / rel).exists()]
        then = "useful-views sync --all" + (" and useful-views cookbook" if "cookbook.json" in generated else "")
        raise ProjectError(
            f"{path} has git merge conflict markers. It is generated, so do not resolve the conflict by hand: "
            f"take either side (git checkout --ours {' '.join(generated)}), then run {then}, and git add those "
            "files. sync --all runs every recipe again, so their records come from real runs."
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProjectError(f"{path} is not valid JSON ({exc}). {restore}") from exc
    ok = (
        isinstance(data, dict)
        and isinstance(data.get("resources"), list)
        and isinstance(data.get("recipes"), list)
        and all(isinstance(r, dict) and isinstance(r.get("path"), str) for r in data["resources"])
        and all(
            isinstance(r, dict)
            and isinstance(r.get("name"), str)
            and isinstance(r.get("path"), str)
            and isinstance(r.get("inputs"), list)
            and isinstance(r.get("outputs"), list)
            for r in data["recipes"]
        )
    )
    if not ok:
        raise ProjectError(f"{path} does not have the structure useful-views writes. {restore}")
    for record in data["recipes"]:
        bad = [o for o in record["outputs"] if not (isinstance(o, str) and RECORD_OUTPUT.match(o))]
        if not RECORD_NAME.match(record["name"]) or bad:
            what = f"output {bad[0]!r}" if bad else f"recipe name {record['name']!r}"
            raise ProjectError(
                f"{path} records a {what} that useful-views never writes (it was edited by hand, or left by a "
                f"merge), so nothing is changed. {restore}"
            )
    for resource in data["resources"]:
        if not _fingerprints_ok(resource):
            raise ProjectError(
                f"{path} records a fingerprint for {resource['path']} that useful-views never writes (it was "
                f"edited by hand, or left by a merge), so nothing is changed. {restore}"
            )
    return data


def _fingerprints_ok(resource: dict) -> bool:
    """A view's fingerprints, when recorded, have the shape useful-views
    writes. Records from before fingerprints have none, which is fine."""
    def ok(value: object) -> bool:
        return value is None or (isinstance(value, str) and FINGERPRINT.match(value) is not None)

    produced = resource.get("producedBy")
    if produced is None:
        return "fingerprint" not in resource
    if not isinstance(produced, dict) or not ok(resource.get("fingerprint")):
        return False
    inputs = produced.get("inputFingerprints", {})
    return isinstance(inputs, dict) and all(isinstance(k, str) and ok(v) for k, v in inputs.items())


def raw_inputs(record: dict) -> list[str]:
    return [p for p in record["inputs"] if not p.startswith(f"{VIEWS}/")]


# What useful-views itself writes in a record; anything else was put there by
# hand or by a bad merge, and must never be used to name a file to delete.
RECORD_NAME = re.compile(r"^[a-z0-9_]+$")
RECORD_OUTPUT = re.compile(rf"^{re.escape(VIEWS)}/(?!\.\.?$)[^/\\]+$")

# A Data Package name: lowercase letters, digits, '.', '_' and '-'.
PACKAGE_NAME = re.compile(r"^[a-z0-9._-]+$")


def assemble(
    root: Path,
    records: list[dict],
    views: dict[str, dict],
    raws: dict[str, dict],
    name: object = None,
) -> dict:
    """Build datapackage.json from records and resource descriptions.

    `views` and `raws` map paths to resources. Only raw files read by a record
    are kept. Recipes are listed in run order (see `run_order`), and views
    under their recipe, so the file reads in the order the data is made.
    `name` is the package name already recorded: it is kept, so a clone in a
    folder with another name writes the same file. Only when there is none
    (or it is not a valid name) does the folder's name give one.
    """
    if not (isinstance(name, str) and PACKAGE_NAME.fullmatch(name)):
        name = slug(root.name)
    by_name = {r["name"]: r for r in records}
    records = [by_name[n] for n in run_order(by_name)]
    used_raw = sorted({p for r in records for p in raw_inputs(r)})
    resources = [raws[p] for p in used_raw if p in raws]
    for record in records:
        for path in sorted(record["outputs"]):
            if path in views:
                resources.append(view_layout(views[path]))
    return {"name": name, "resources": resources, "recipes": records}


def run_order(records: dict[str, dict]) -> list[str]:
    """Recipe names, upstream first; recipes that do not depend on each other
    in name order."""
    makers = {out: name for name, r in records.items() for out in r["outputs"]}
    upstream = {
        name: sorted({makers[i] for i in r["inputs"] if i in makers} - {name}) for name, r in records.items()
    }
    order: list[str] = []
    done: set[str] = set()

    def visit(name: str, seen: tuple[str, ...] = ()) -> None:
        if name in done or name in seen:
            return
        for up in upstream[name]:
            visit(up, (*seen, name))
        done.add(name)
        order.append(name)

    for name in sorted(records):
        visit(name)
    return order


def fingerprints_differ(then: object, now: object) -> bool | None:
    """Whether two fingerprints show different data: True or False, or None
    when that is unknown, because one is missing or null, or because they
    were made under different rules (datahash1 and datahash2 are never
    compared)."""
    if not (isinstance(then, str) and isinstance(now, str)):
        return None
    if then.rsplit(":", 1)[0] != now.rsplit(":", 1)[0]:
        return None
    return then != now


def stale_inputs(data: dict) -> dict[str, list[str]]:
    """For each recorded recipe, the views it read that were made again with
    different data since it ran: the fingerprint its views recorded for them
    no longer matches their own. Where that is unknown (fingerprints_differ),
    it never counts as a change."""
    views, _ = split_resources(data)
    found: dict[str, set[str]] = {}
    for view in views.values():
        produced = view["producedBy"]
        for path, then in (produced.get("inputFingerprints") or {}).items():
            if fingerprints_differ(then, views.get(path, {}).get("fingerprint")):
                found.setdefault(produced.get("recipe"), set()).add(path)
    return {name: sorted(paths) for name, paths in found.items()}


def unfingerprinted(data: dict) -> list[str]:
    """Views recorded without a fingerprint, by an earlier version of
    useful-views. (A view whose fingerprint is null has one that cannot be
    computed; it is not listed.)"""
    views, _ = split_resources(data)
    return sorted(path for path, view in views.items() if "fingerprint" not in view)


def split_resources(data: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Existing resources as (views by path, raws by path)."""
    views, raws = {}, {}
    for resource in data["resources"]:
        (views if "producedBy" in resource else raws)[resource["path"]] = resource
    return views, raws


WIDTH = 100


def render_datapackage(data: dict) -> str:
    """datapackage.json, laid out to be read: the package, its two lists and
    each of their entries one item per line; inside an entry, anything that
    fits on a line (a column, a list of paths) on one line."""
    return _layout(data, 0, 0, 0) + "\n"


def _layout(value: object, indent: int, depth: int, lead: int, fits: bool | None = None) -> str:
    """`value` as JSON for a line that starts `indent` spaces and `lead`
    characters (its key) in. Items of one list are all on one line each, or
    all spread out, so they line up."""
    flat = json.dumps(value, ensure_ascii=False)
    if fits is None:
        fits = indent + lead + len(flat) <= WIDTH
    if not isinstance(value, (dict, list)) or not value or (depth > 2 and fits):
        return flat
    pad = " " * (indent + 2)
    if isinstance(value, dict):
        items = []
        for key, item in value.items():
            name = json.dumps(key, ensure_ascii=False) + ": "
            items.append(pad + name + _layout(item, indent + 2, depth + 1, len(name)))
        return "{\n" + ",\n".join(items) + "\n" + " " * indent + "}"
    together = all(indent + 2 + len(json.dumps(item, ensure_ascii=False)) <= WIDTH for item in value)
    items = [pad + _layout(item, indent + 2, depth + 1, 0, together) for item in value]
    return "[\n" + ",\n".join(items) + "\n" + " " * indent + "]"


# --- useful-views/README.md -------------------------------------------------

CATALOG_HEADER = (
    "# Useful views\n\n"
    "Generated by useful-views from `datapackage.json`. Do not edit; it is rewritten on every run."
)


def cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").splitlines())


def render_catalog(data: dict) -> str:
    """The catalog of views, generated from datapackage.json alone."""
    records = {r["name"]: r for r in data["recipes"]}
    blocks = [CATALOG_HEADER]
    for resource in data["resources"]:
        produced = resource.get("producedBy")
        if not produced:
            continue
        record = records.get(produced["recipe"], {})
        filename = resource["path"].rsplit("/", 1)[-1]
        inputs = ", ".join(f"`{p}`" for p in produced.get("inputs", []))
        blocks.append(f"## {filename}")
        blocks.append(record.get("description", ""))
        blocks.append(f"- Recipe: `{record.get('path', '')}/`\n- Inputs: {inputs}")
        steps = record.get("steps", [])
        blocks.append("Steps:")
        blocks.append(
            "\n".join(
                f"{i}. {s['description']} (`{s['path'].rsplit('/', 1)[-1]}`)" for i, s in enumerate(steps, 1)
            )
        )
        fields = resource.get("schema", {}).get("fields", [])
        if fields:
            rows = "\n".join(f"| {cell(f['name'])} | {cell(f['type'])} |" for f in fields)
            blocks.append("| Column | Type |\n|---|---|\n" + rows)
        else:
            blocks.append("No columns detected.")
    return "\n\n".join(blocks) + "\n"


# --- writing ----------------------------------------------------------------


def retry(action, path: Path, attempts: int = 5):
    """Run a filesystem action, retrying briefly when another program
    (an editor, a virus scanner, a notebook) holds the file on Windows."""
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


def _temp_for(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.tmp")


def write_files(files: dict[Path, str]) -> list[Path]:
    """Write UTF-8 files with LF line endings, and return those that changed.
    All are written to temporary files first, then each replaces its target,
    so a failure while writing leaves every target unchanged."""
    temps: dict[Path, Path] = {}
    replaced: dict[Path, bytes | None] = {}
    current = next(iter(files))  # the file being written, for the message
    try:
        for path, text in files.items():
            current = path
            data = text.encode("utf-8")
            if path.is_file() and path.read_bytes() == data:
                continue  # unchanged: leave the file, and its timestamp, alone
            if path.is_dir():
                raise IsADirectoryError(21, "it is a folder", str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = _temp_for(path)
            temps[path] = tmp
            with open(tmp, "wb") as f:
                f.write(data)
        for path, tmp in temps.items():
            current = path
            previous = path.read_bytes() if path.exists() else None
            retry(lambda: os.replace(tmp, path), path)
            replaced[path] = previous
        return list(replaced)
    except (OSError, UnicodeError) as exc:
        for path, previous in replaced.items():  # put back what was already replaced
            try:
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)
            except OSError:
                pass
        raise ProjectError(
            f"Could not write {current}: {getattr(exc, 'strerror', None) or exc}. If another program has it open, "
            "close it, check that the folder is writable, and try again."
        ) from exc
    finally:
        for tmp in temps.values():
            if tmp.exists():
                tmp.unlink()


def write_atomic(path: Path, text: str) -> None:
    write_files({path: text})


def write_generated(root: Path, data: dict) -> list[Path]:
    """Write datapackage.json and the catalog derived from it; return the
    files that changed."""
    # The catalog goes first, so that if writing fails part way the record
    # (datapackage.json) still describes the previous state.
    return write_files({root / CATALOG: render_catalog(data), root / DATAPACKAGE: render_datapackage(data)})

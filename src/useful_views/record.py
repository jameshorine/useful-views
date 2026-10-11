"""Describing files and writing datapackage.json and useful-views/README.md.

datapackage.json is the record store: its `recipes` list holds one record
per recorded recipe, and its `resources` list describes the raw files those
recipes read and the views they made ("Generated files" in the README).
"""

from __future__ import annotations

import codecs
import json
import os
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from .errors import ProjectError
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


def is_utf8(path: Path) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


# The forms Frictionless accepts for its default time and datetime formats.
# Values pyarrow parses in any other form are recorded as strings.
DEFAULT_FORMAT = {
    "time": r"^\d{2}:\d{2}:\d{2}(\.\d+)?$",
    "datetime": r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$",
}
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
    if not is_utf8(path):
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
        for column in table.columns[1:]:
            blank = pc.and_(blank, pc.is_null(column))
        if pc.any(blank).as_py():
            return describe_file(rel)

    fields = schema_of(table.schema)["fields"]
    checks = {f["name"]: DEFAULT_FORMAT[f["type"]] for f in fields if f["type"] in DEFAULT_FORMAT}
    if checks:
        as_text = pacsv.read_csv(
            path,
            parse_options=parse,
            convert_options=pacsv.ConvertOptions(
                null_values=[""], strings_can_be_null=True, column_types={n: pa.string() for n in checks}
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


def describe_view(root: Path, rel: str, recipe: str, inputs: list[str]) -> dict:
    resource = describe_parquet(root, rel)
    resource["producedBy"] = {"recipe": recipe, "inputs": list(inputs)}
    return resource


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
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectError(f"{path} is missing. {restore}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
    return data


def raw_inputs(record: dict) -> list[str]:
    return [p for p in record["inputs"] if not p.startswith(f"{VIEWS}/")]


def assemble(
    root: Path,
    records: list[dict],
    views: dict[str, dict],
    raws: dict[str, dict],
) -> dict:
    """Build datapackage.json from records and resource descriptions.

    `views` and `raws` map paths to resources. Only raw files read by a record
    are kept; views are listed under their recipe, recipes in name order.
    """
    records = sorted(records, key=lambda r: r["name"])
    used_raw = sorted({p for r in records for p in raw_inputs(r)})
    resources = [raws[p] for p in used_raw if p in raws]
    for record in records:
        for path in sorted(record["outputs"]):
            if path in views:
                resources.append(views[path])
    return {"name": slug(root.name), "resources": resources, "recipes": records}


def split_resources(data: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Existing resources as (views by path, raws by path)."""
    views, raws = {}, {}
    for resource in data["resources"]:
        (views if "producedBy" in resource else raws)[resource["path"]] = resource
    return views, raws


def render_datapackage(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


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


def write_files(files: dict[Path, str]) -> None:
    """Write UTF-8 files with LF line endings. All are written to temporary
    files first, then each replaces its target, so a failure while writing
    leaves every target unchanged."""
    temps: dict[Path, Path] = {}
    replaced: dict[Path, bytes | None] = {}
    try:
        for path, text in files.items():
            data = text.encode("utf-8")
            if path.is_file() and path.read_bytes() == data:
                continue  # unchanged: leave the file, and its timestamp, alone
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = _temp_for(path)
            temps[path] = tmp
            with open(tmp, "wb") as f:
                f.write(data)
        for path, tmp in temps.items():
            previous = path.read_bytes() if path.exists() else None
            retry(lambda: os.replace(tmp, path), path)
            replaced[path] = previous
    except (OSError, UnicodeError) as exc:
        for path, previous in replaced.items():  # put back what was already replaced
            try:
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)
            except OSError:
                pass
        name = getattr(exc, "filename", None) or next(iter(files))
        raise ProjectError(
            f"Could not write {name}: {getattr(exc, 'strerror', None) or exc}. If another program has it open, close it, "
            "check that the folder is writable, and try again."
        ) from exc
    finally:
        for tmp in temps.values():
            if tmp.exists():
                tmp.unlink()


def write_atomic(path: Path, text: str) -> None:
    write_files({path: text})


def write_generated(root: Path, data: dict) -> None:
    """Write datapackage.json and the catalog derived from it."""
    # The catalog goes first, so that if writing fails part way the record
    # (datapackage.json) still describes the previous state.
    write_files({root / CATALOG: render_catalog(data), root / DATAPACKAGE: render_datapackage(data)})

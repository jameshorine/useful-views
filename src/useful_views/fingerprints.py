"""Fingerprints of views.

A view's data hash is a fingerprint of its contents: its columns, in order,
with their names and types, and its values, row by row. It does not depend on
the library that wrote the file, its version, or how the file is compressed
and encoded. The rules are fixed and versioned (``datahash1``); they are set
out in user_guide/reference/data-hash.md, and this module follows them.

Values are encoded with pyarrow compute functions, one batch of one column at
a time, so a large view is never held in memory whole, however its file was
written.
"""

from __future__ import annotations

import hashlib
import os
import stat
import struct
import sys
from decimal import Decimal

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .errors import UsefulViewsError

PREFIX = "datahash1:sha256:"

# Rows read and hashed at a time, however large the file's row groups:
# pyarrow's own default batch size. Measured on 10 million rows, smaller
# batches use no less memory and larger ones use more, at the same speed.
BATCH_ROWS = 65_536

_BYTES = pa.large_binary()
_PRESENT = pa.scalar(b"\x01", _BYTES)
_MISSING = pa.scalar(b"\x00", _BYTES)
_NOTHING = pa.scalar(b"", _BYTES)
# The one NaN every NaN is written as.
_NAN = pa.scalar(struct.unpack("<d", bytes.fromhex("000000000000f87f"))[0], pa.float64())
_PER_SECOND = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}
# Parquet logical types that pyarrow versions read differently (as binary,
# as string, or as an extension type): their canonical type is fixed here,
# from the file's own label, so every version gives the same hash. GEOMETRY
# and GEOGRAPHY are not covered (None): pyarrow reads them as binary or as an
# extension type depending on what else a program has loaded.
_LOGICAL = {"JSON": "string", "UUID": "binary", "GEOMETRY": None, "GEOGRAPHY": None}
_NUMBERS = {
    pa.int8(): "int8", pa.int16(): "int16", pa.int32(): "int32", pa.int64(): "int64",
    pa.uint8(): "uint8", pa.uint16(): "uint16", pa.uint32(): "uint32", pa.uint64(): "uint64",
    pa.float16(): "float16", pa.float32(): "float32", pa.float64(): "float64",
}


class DataHashError(UsefulViewsError):
    """A file's data hash could not be computed.

    Attributes:
        path: The file.
        column: The column whose type the rules do not cover (a list, struct
            or map in a cell, for example), or None otherwise: the file could
            not be read, or the computer is not little-endian.

    Added in version 0.2.0.
    """

    def __init__(self, message: str, *, path: str, column: str | None = None):
        super().__init__(message)
        self.path = path
        self.column = column

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update(path=self.path, column=self.column)
        return data


def canonical_type(t: pa.DataType) -> str | None:
    """The type's name under the rules, or None when version 1 does not cover it."""
    types = pa.types
    if isinstance(t, pa.BaseExtensionType):
        return None  # other than JSON and UUID, which the file labels (see _LOGICAL)
    if types.is_dictionary(t):
        return canonical_type(t.value_type)
    if t in _NUMBERS:
        return _NUMBERS[t]
    if types.is_null(t):
        return "null"
    if types.is_boolean(t):
        return "bool"
    if types.is_decimal(t):
        return f"decimal({t.precision},{t.scale})"
    if types.is_string(t) or types.is_large_string(t) or _is(t, "is_string_view"):
        return "string"
    if (types.is_binary(t) or types.is_large_binary(t) or types.is_fixed_size_binary(t)
            or _is(t, "is_binary_view")):
        return "binary"
    if types.is_date(t):
        return "date"
    if types.is_timestamp(t):
        # With a time zone the values are instants in UTC, and the zone only says
        # how to show them; writers do not all keep its name, so it does not count.
        return "timestamp" if t.tz is None else "timestamp[UTC]"
    if types.is_time(t):
        return "time"
    if types.is_duration(t):
        return "duration"
    return None


def _is(t: pa.DataType, test: str) -> bool:
    """A type test that older pyarrow versions do not have counts as False."""
    return getattr(pa.types, test, lambda _: False)(t)


def data_hash(path: str | os.PathLike) -> str:
    """The data hash of a parquet file: a fingerprint of the table it holds.

    Two files that hold the same table, column for column and row for row,
    have the same data hash, whichever library wrote them. The rules are set
    out in the documentation's "Data hash rules" page:
    https://jameshorine.github.io/useful-views/user-guide/reference/data-hash.html

    For example, `uvs.data_hash("useful-views/gdp-per-capita.parquet")`
    gives `'datahash1:sha256:69378218…'`. A relative path starts from the
    current folder; from a notebook in a subfolder of the project, use
    `uvs.data_hash(uvs.root() / "useful-views/gdp-per-capita.parquet")`.

    Added in version 0.2.0.

    Args:
        path: The parquet file.

    Returns:
        The hash, as ``datahash1:sha256:`` and 64 hexadecimal digits.

    Raises:
        DataHashError: The file cannot be read as parquet, or a column holds a
            type the rules do not cover yet, such as a list or a struct in each
            cell.
    """
    return hash_file(path, os.fspath(path))


def hash_file(path: str | os.PathLike, shown: str) -> str:
    """data_hash, naming the file `shown` in messages (a path relative to the
    project, so messages carry no one's home folder)."""
    if sys.byteorder != "little":
        raise DataHashError("Data hashes can only be computed on little-endian computers.", path=shown)
    if not os.fspath(path):
        raise DataHashError("An empty path names no file.", path=shown)
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):  # a folder, a device or a pipe would hang or fail
            raise DataHashError(f"{shown} is not a file.", path=shown)
        source = pa.OSFile(os.fspath(path))
    except FileNotFoundError as error:
        raise DataHashError(f"{shown} does not exist.", path=shown) from error
    except ValueError as error:  # a NUL character
        raise DataHashError(f"{shown!r} is not a valid path.", path=shown) from error
    except OSError as error:
        raise DataHashError(f"{shown} could not be opened: {_reason(path)}.", path=shown) from error
    # Closed when done, even on an error, so the file can be replaced or
    # deleted straight away (Windows refuses while it is open).
    with source:
        try:
            parquet = pq.ParquetFile(source)
        except (OSError, pa.ArrowException, ValueError) as error:  # ValueError: a name that is not UTF-8
            raise DataHashError(f"{shown} could not be read as a parquet file: {error}", path=shown) from error
        with parquet:
            try:
                return _digest(parquet, shown)
            except DataHashError:
                raise
            except (OSError, pa.ArrowException, ValueError, OverflowError, struct.error) as error:
                # A damaged file: unreadable names, impossible counts, bad pages.
                raise DataHashError(f"{shown} could not be read as a parquet file: {error}", path=shown) from error


def _reason(path: str | os.PathLike) -> str:
    """Why a file could not be opened, as Python says it: pyarrow's message
    (its strerror too) holds the full path."""
    try:
        with open(path, "rb"):
            pass
    except OSError as plain:
        if plain.strerror:
            return plain.strerror
    return "it could not be read"


def _digest(parquet: pq.ParquetFile, name: str) -> str:
    schema = parquet.schema_arrow
    kinds = []
    for i, field in enumerate(schema):
        # An extension type is not covered, whether this pyarrow knows it (an
        # extension type) or not (its storage type, labelled in the metadata):
        # either way the file says so, so the answer does not depend on which
        # pyarrow reads it, or on what else a program has loaded.
        extension = (field.type.extension_name if isinstance(field.type, pa.BaseExtensionType)
                     else (field.metadata or {}).get(b"ARROW:extension:name", b"").decode("utf-8", "replace"))
        kind = None if extension else canonical_type(field.type)
        # A flat column is the file's column i, with the file's own label.
        logical = None if pa.types.is_nested(field.type) else parquet.schema.column(i).logical_type.type
        if logical in _LOGICAL:
            kind = _LOGICAL[logical]
        if kind is None:
            what = (f"labelled {logical} in the file" if logical in _LOGICAL
                    else f"of the extension type {extension}" if extension else str(field.type))
            raise DataHashError(
                f"{name} has no data hash: column {field.name!r} is {what}, a type data hash version 1 "
                "does not cover (it covers numbers, text, bytes, booleans, dates, times and decimals, not "
                "lists, structs, maps, extension types or other types).",
                path=name, column=field.name,
            )
        kinds.append(kind)

    digests = [hashlib.sha256() for _ in kinds]
    names = schema.names
    unique = len(set(names)) == len(names)
    groups = _bundles(parquet)
    if unique:
        # One column at a time, a few row groups at a time, in batches of
        # BATCH_ROWS rows: what is in memory is one batch, and the
        # compressed bytes of one column of those row groups, however large
        # the file. (Batches across the whole file would load a column's
        # compressed bytes for every row group at once.)
        for digest, kind, column_name in zip(digests, kinds, names):
            for group in groups:
                for batch in parquet.iter_batches(batch_size=BATCH_ROWS, row_groups=group,
                                                  columns=[column_name]):
                    digest.update(_encode(batch.column(0), kind))
    else:
        # Duplicate names cannot be read by name: every column, a batch at a time.
        for group in groups:
            for batch in parquet.iter_batches(batch_size=BATCH_ROWS, row_groups=group):
                for digest, kind, column in zip(digests, kinds, batch.columns):
                    digest.update(_encode(column, kind))

    total = hashlib.sha256(b"datahash1\n")
    total.update(struct.pack("<QQ", parquet.metadata.num_rows, len(names)))
    for field_name, kind, digest in zip(names, kinds, digests):
        _length_prefixed(total, field_name.encode("utf-8"))
        _length_prefixed(total, kind.encode("ascii"))
        total.update(digest.digest())
    return PREFIX + total.hexdigest()


def _bundles(parquet: pq.ParquetFile) -> list[list[int]]:
    """The file's row groups, in order, gathered until each bundle holds at
    least BATCH_ROWS rows: many small row groups are read in few calls (each
    call has a fixed cost), and a large one is read on its own."""
    bundles: list[list[int]] = []
    bundle: list[int] = []
    rows = 0
    for group in range(parquet.metadata.num_row_groups):
        bundle.append(group)
        rows += parquet.metadata.row_group(group).num_rows
        if rows >= BATCH_ROWS:
            bundles.append(bundle)
            bundle, rows = [], 0
    if bundle:
        bundles.append(bundle)
    return bundles


def _length_prefixed(digest, data: bytes) -> None:
    digest.update(struct.pack("<Q", len(data)))
    digest.update(data)


def _encode(chunk: pa.Array, kind: str) -> bytes | memoryview:
    """Every row of a column chunk, encoded: 0x00 when missing, else 0x01 and the value."""
    if isinstance(chunk, pa.ExtensionArray):  # JSON or UUID, read as an extension type
        chunk = chunk.storage
    if kind == "null":
        return bytes(len(chunk))
    if pa.types.is_dictionary(chunk.type):
        chunk = chunk.dictionary_decode()
    rows = pc.fill_null(pc.binary_join_element_wise(_PRESENT, _values(chunk, kind), _NOTHING), _MISSING)
    return _contents(rows)


def _values(chunk: pa.Array, kind: str) -> pa.Array:
    """Each present value's encoding, as large_binary; null where missing."""
    t = chunk.type
    if kind == "bool":
        return _fixed(pc.cast(chunk, pa.uint8()), 1)
    if kind.startswith("uint"):
        return _fixed(pc.cast(chunk, pa.uint64()), 8)
    if kind.startswith("int"):
        return _fixed(pc.cast(chunk, pa.int64()), 8)
    if kind.startswith("float"):
        x = pc.cast(chunk, pa.float64())
        x = pc.if_else(pc.equal(x, 0.0), 0.0, x)  # -0.0 is 0.0
        x = pc.if_else(pc.is_nan(x), _NAN, x)
        return _fixed(x, 8)
    if kind.startswith("decimal"):
        # The unscaled integer as a signed 256-bit number, whatever the storage
        # width: its own little-endian bytes, then sign extension (0xFF bytes
        # for a negative number, 0x00 otherwise) up to 32 bytes.
        width = t.byte_width
        raw = _fixed(chunk, width)
        if width == 32:
            return raw
        pad = 32 - width
        negative = pc.less(chunk, pa.scalar(Decimal(0), t))  # 0 of the column's own type: any scale fits
        sign = pc.if_else(negative, pa.scalar(b"\xff" * pad, _BYTES), pa.scalar(b"\x00" * pad, _BYTES))
        return pc.binary_join_element_wise(raw, sign, _NOTHING)
    if kind in ("string", "binary"):
        data = pc.cast(chunk, _BYTES)
        lengths = pc.cast(pc.binary_length(data), pa.uint64())
        return pc.binary_join_element_wise(_fixed(lengths, 8), data, _NOTHING)
    if kind == "date":
        if pa.types.is_date32(t):
            days = pc.cast(chunk.view(pa.int32()), pa.int64())
        else:
            days, _ = _floor_divide(chunk.view(pa.int64()), 86_400_000)
        return _fixed(days, 8)
    if kind == "time":
        raw = pc.cast(chunk.view(pa.int32() if pa.types.is_time32(t) else pa.int64()), pa.int64())
        return _fixed(pc.multiply(raw, 1_000_000_000 // _PER_SECOND[t.unit]), 8)
    # timestamp or duration: whole seconds, then nanoseconds within the second
    per_second = _PER_SECOND[t.unit]
    seconds, rest = _floor_divide(chunk.view(pa.int64()), per_second)
    nanoseconds = pc.cast(pc.multiply(rest, 1_000_000_000 // per_second), pa.uint32())
    return pc.binary_join_element_wise(_fixed(seconds, 8), _fixed(nanoseconds, 4), _NOTHING)


def _fixed(values: pa.Array, width: int) -> pa.Array:
    """Each value's own bytes (little-endian), as large_binary."""
    return pc.cast(values.view(pa.binary(width)), _BYTES)


def _floor_divide(values: pa.Array, divisor: int) -> tuple[pa.Array, pa.Array]:
    """Quotient rounded down and the non-negative remainder, as int64."""
    quotient = pc.divide(values, divisor)  # rounds toward zero
    rest = pc.subtract(values, pc.multiply(quotient, divisor))
    below = pc.less(rest, 0)
    return (pc.if_else(below, pc.subtract(quotient, 1), quotient),
            pc.if_else(below, pc.add(rest, divisor), rest))


def _contents(rows: pa.Array) -> memoryview:
    """The bytes of a large_binary array with no nulls, one value after another."""
    if len(rows) == 0:
        return memoryview(b"")
    _, offsets, data = rows.buffers()
    offsets = memoryview(offsets)
    start = rows.offset * 8
    end = (rows.offset + len(rows)) * 8
    first = int.from_bytes(offsets[start:start + 8], "little", signed=True)
    last = int.from_bytes(offsets[end:end + 8], "little", signed=True)
    return memoryview(data)[first:last]

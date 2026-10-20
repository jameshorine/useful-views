"""Fingerprints of views.

A view's data hash is a fingerprint of its contents: its columns, in order,
with their names and types, and its values, row by row. It does not depend on
the library that wrote the file, its version, or how the file is compressed
and encoded. The rules are fixed and versioned (``datahash1``); they are set
out in user_guide/reference/data-hash.md, and this module follows them.

Values are encoded with pyarrow compute functions, a column chunk at a time,
so a large view is never held in memory whole.
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .errors import UsefulViewsError

PREFIX = "datahash1:sha256:"

_BYTES = pa.large_binary()
_PRESENT = pa.scalar(b"\x01", _BYTES)
_MISSING = pa.scalar(b"\x00", _BYTES)
_NOTHING = pa.scalar(b"", _BYTES)
# The one NaN every NaN is written as.
_NAN = pa.scalar(struct.unpack("<d", bytes.fromhex("000000000000f87f"))[0], pa.float64())
_PER_SECOND = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}
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
            or map in a cell, for example), or None when the file itself could
            not be read.
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
        return "timestamp" if t.tz is None else f"timestamp[{t.tz}]"
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
    out in the documentation's "Data hash rules" page.

    Args:
        path: The parquet file.

    Returns:
        The hash, as ``datahash1:sha256:`` and 64 hexadecimal digits.

    Raises:
        DataHashError: The file cannot be read as parquet, or a column holds a
            type the rules do not cover yet, such as a list or a struct in each
            cell.
    """
    name = os.fspath(path)
    if sys.byteorder != "little":
        raise DataHashError("Data hashes can only be computed on little-endian computers.", path=name)
    try:
        parquet = pq.ParquetFile(path)
        schema = parquet.schema_arrow
    except (OSError, pa.ArrowException) as error:
        raise DataHashError(f"{name} could not be read as a parquet file: {error}", path=name) from error

    kinds = []
    for field in schema:
        kind = canonical_type(field.type)
        if kind is None:
            raise DataHashError(
                f"{name} has no data hash: column {field.name!r} is {field.type}, and data hash "
                "version 1 covers flat tables only (no lists, structs or maps in a cell).",
                path=name, column=field.name,
            )
        kinds.append(kind)

    digests = [hashlib.sha256() for _ in kinds]
    names = schema.names
    try:
        for group in range(parquet.num_row_groups):
            if len(set(names)) == len(names):
                # One column at a time, so only one column chunk is in memory.
                columns = [parquet.read_row_group(group, columns=[n]).column(0) for n in names]
            else:
                # Duplicate names cannot be read by name.
                columns = parquet.read_row_group(group).columns
            for digest, kind, column in zip(digests, kinds, columns):
                for chunk in column.chunks:
                    digest.update(_encode(chunk, kind))
    except (OSError, pa.ArrowException) as error:
        raise DataHashError(f"{name} could not be read as a parquet file: {error}", path=name) from error

    total = hashlib.sha256(b"datahash1\n")
    total.update(struct.pack("<QQ", parquet.metadata.num_rows, len(names)))
    for field_name, kind, digest in zip(names, kinds, digests):
        _length_prefixed(total, field_name.encode("utf-8"))
        _length_prefixed(total, kind.encode("ascii"))
        total.update(digest.digest())
    return PREFIX + total.hexdigest()


def _length_prefixed(digest, data: bytes) -> None:
    digest.update(struct.pack("<Q", len(data)))
    digest.update(data)


def _encode(chunk: pa.Array, kind: str) -> bytes | memoryview:
    """Every row of a column chunk, encoded: 0x00 when missing, else 0x01 and the value."""
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
        # The unscaled integer as a signed 256-bit number, whatever the storage width.
        raw = chunk.view(pa.binary(t.byte_width)).to_pylist()
        return pa.array(
            [None if b is None else int.from_bytes(b, "little", signed=True).to_bytes(32, "little", signed=True)
             for b in raw],
            _BYTES,
        )
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

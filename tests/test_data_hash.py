"""Data hashes follow the rules in user_guide/reference/data-hash.md.

`reference_hash` below is a second, slow implementation written straight from
that page, value by value in plain Python. `data_hash` must always agree with
it. The files in tests/data/datahash/ were written once by `write_vectors`,
with pyarrow 26, polars 2.0 and pandas 3.0. Never rewrite them: they pin the
rules, so that every pyarrow version CI tests computes the same hashes.
"""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import math
import struct
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest

import useful_views as uvs
from useful_views.fingerprints import _encode, canonical_type

ROOT = Path(__file__).resolve().parent.parent
VECTORS = ROOT / "tests" / "data" / "datahash"

EXPECTED = {
    "readings-polars.parquet": "datahash1:sha256:76c92a8bef6fae8ffaffcd6a3558af6146eee1f608168632539e42accdebbc18",
    "readings-pyarrow.parquet": "datahash1:sha256:76c92a8bef6fae8ffaffcd6a3558af6146eee1f608168632539e42accdebbc18",
    "readings-pandas.parquet": "datahash1:sha256:76c92a8bef6fae8ffaffcd6a3558af6146eee1f608168632539e42accdebbc18",
    "every-type-pyarrow.parquet": "datahash1:sha256:be2d9a2855e04310f28773dec7fe36e2b8c7132123dab5e857c7b5727f607599",
    "empty-pyarrow.parquet": "datahash1:sha256:8e56d38baf9f412623d9c8977e6cd189eeda02d75c384e520aa772b2f6c43a51",
}


# --- The rules, written out by hand -----------------------------------------

CANONICAL_NAN = bytes.fromhex("000000000000f87f")
PER_SECOND = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}


def length_prefixed(data: bytes) -> bytes:
    return struct.pack("<Q", len(data)) + data


def kind(t: pa.DataType) -> str:
    """The canonical type name, from the table on the rules page."""
    if pa.types.is_dictionary(t):
        return kind(t.value_type)
    names = {
        "halffloat": "float16", "float": "float32", "double": "float64", "bool": "bool", "null": "null",
        "string": "string", "large_string": "string", "string_view": "string",
        "binary": "binary", "large_binary": "binary", "binary_view": "binary",
        "date32[day]": "date", "date64[ms]": "date",
    }
    if str(t) in names:
        return names[str(t)]
    if pa.types.is_integer(t):
        return str(t)
    if pa.types.is_fixed_size_binary(t):
        return "binary"
    if pa.types.is_decimal(t):
        return f"decimal({t.precision},{t.scale})"
    if pa.types.is_timestamp(t):
        return "timestamp" if t.tz is None else f"timestamp[{t.tz}]"
    if pa.types.is_time(t):
        return "time"
    if pa.types.is_duration(t):
        return "duration"
    raise AssertionError(f"not covered: {t}")


def plain_values(column: pa.ChunkedArray, t: pa.DataType) -> list:
    """Python values; dates and times as the integers the file stores."""
    if pa.types.is_date32(t) or pa.types.is_time32(t):
        return pc.cast(column, pa.int32()).to_pylist()
    if pa.types.is_date64(t) or pa.types.is_time64(t) or pa.types.is_timestamp(t) or pa.types.is_duration(t):
        return pc.cast(column, pa.int64()).to_pylist()
    return column.to_pylist()


def encode(value, t: pa.DataType) -> bytes:
    """One present value, from the table on the rules page."""
    if pa.types.is_dictionary(t):
        return encode(value, t.value_type)
    if pa.types.is_boolean(t):
        return b"\x01" if value else b"\x00"
    if pa.types.is_unsigned_integer(t):
        return struct.pack("<Q", value)
    if pa.types.is_integer(t):
        return struct.pack("<q", value)
    if pa.types.is_floating(t):
        value = float(value)
        if math.isnan(value):
            return CANONICAL_NAN
        return struct.pack("<d", 0.0 if value == 0 else value)
    if pa.types.is_decimal(t):
        unscaled = int(value.scaleb(t.scale, decimal.Context(prec=100)))
        return unscaled.to_bytes(32, "little", signed=True)
    if kind(t) == "string":
        return length_prefixed(value.encode("utf-8"))
    if kind(t) == "binary":
        return length_prefixed(value)
    if pa.types.is_date32(t):
        return struct.pack("<q", value)
    if pa.types.is_date64(t):
        return struct.pack("<q", value // 86_400_000)
    if pa.types.is_time(t):
        return struct.pack("<q", value * (1_000_000_000 // PER_SECOND[t.unit]))
    seconds, rest = divmod(value, PER_SECOND[t.unit])
    return struct.pack("<qI", seconds, rest * (1_000_000_000 // PER_SECOND[t.unit]))


def reference_hash(path) -> str:
    table = pq.ParquetFile(path).read()  # read_table refuses duplicate column names
    stream = b"datahash1\n" + struct.pack("<QQ", table.num_rows, table.num_columns)
    for field, column in zip(table.schema, table.columns):
        rows = b"".join(
            b"\x00" if v is None else b"\x01" + encode(v, field.type) for v in plain_values(column, field.type)
        )
        stream += length_prefixed(field.name.encode("utf-8")) + length_prefixed(kind(field.type).encode("ascii"))
        stream += hashlib.sha256(rows).digest()
    return "datahash1:sha256:" + hashlib.sha256(stream).hexdigest()


# --- Test tables ------------------------------------------------------------

def floats(bits: list[int | None], t: pa.DataType = pa.float64()) -> pa.Array:
    """A float array with exact bit patterns, None for missing."""
    width = t.bit_width // 8
    validity = sum(1 << i for i, b in enumerate(bits) if b is not None).to_bytes(len(bits) // 8 + 1, "little")
    data = b"".join((b or 0).to_bytes(width, "little") for b in bits)
    return pa.Array.from_buffers(t, len(bits), [pa.py_buffer(validity), pa.py_buffer(data)])


def every_type() -> pa.Table:
    """A table with every type the rules cover, the edges of each, and missing values."""
    d = decimal.Decimal
    return pa.table({
        "flag": pa.array([True, False, None, True]),
        "int8": pa.array([-128, 127, None, 0], pa.int8()),
        "int16": pa.array([-32768, 32767, None, 0], pa.int16()),
        "int32": pa.array([-2**31, 2**31 - 1, None, 0], pa.int32()),
        "int64": pa.array([-2**63, 2**63 - 1, None, 0], pa.int64()),
        "uint8": pa.array([0, 255, None, 1], pa.uint8()),
        "uint16": pa.array([0, 65535, None, 1], pa.uint16()),
        "uint32": pa.array([0, 2**32 - 1, None, 1], pa.uint32()),
        "uint64": pa.array([0, 2**64 - 1, None, 1], pa.uint64()),
        "float16": floats([0x3E00, 0x8000, None, 0x7E01], pa.float16()),  # 1.5, -0.0, missing, a NaN
        "float32": pa.array([1.1, -0.0, None, math.inf], pa.float32()),
        "float64": floats([0x3FE3333333333334, 0x8000000000000000, None, 0xFFF8000000000001]),
        "nan": pa.array([math.nan, None, -math.nan, 0.5]),
        "decimal": pa.array([d("-1.500"), d("123456.789"), None, d("0.000")], pa.decimal128(9, 3)),
        "decimal256": pa.array([d("-1." + "0" * 10), None, d("9" * 30 + ".5" + "0" * 9), d(0)], pa.decimal256(40, 10)),
        "text": pa.array(["", "é", None, "ab"]),
        "large_text": pa.array(["a", "b", None, "日本"], pa.large_string()),
        "bytes": pa.array([b"", b"\x00\xff", None, b"ab"]),
        "fixed": pa.array([b"ab", b"\x00\x00", None, b"zz"], pa.binary(2)),
        "date32": pa.array([dt.date(1969, 12, 31), dt.date(1970, 1, 1), None, dt.date(9999, 12, 31)]),
        "date64": pa.array([-86_400_000, 0, None, 86_400_000 * 20_000], pa.date64()),
        "ts_s": pa.array([-1, 0, None, 253402300799], pa.timestamp("s")),
        "ts_ms_utc": pa.array([-1, 1_500_000_000_123, None, 0], pa.timestamp("ms", tz="UTC")),
        "ts_us": pa.array([-1_000_001, 1, None, 253402300799_999_999], pa.timestamp("us")),
        "ts_ns_tz": pa.array([-1, 1_600_000_000_123_456_789, None, 0], pa.timestamp("ns", tz="America/Sao_Paulo")),
        "time32_s": pa.array([0, 86399, None, 1], pa.time32("s")),
        "time32_ms": pa.array([0, 86_399_999, None, 1], pa.time32("ms")),
        "time64_us": pa.array([0, 86_399_999_999, None, 1], pa.time64("us")),
        "time64_ns": pa.array([0, 86_399_999_999_999, None, 1], pa.time64("ns")),
        "duration_s": pa.array([-1, 0, None, 10**12], pa.duration("s")),
        "duration_ns": pa.array([-1, 1, None, 2**63 - 1], pa.duration("ns")),
        "category": pa.array(["x", None, "y", "x"]).dictionary_encode(),
        "nothing": pa.nulls(4),
    })


def readings_frame():
    import polars as pl

    return pl.DataFrame({
        "station": ["A", "A", "B", "B"],
        "time": ["06:00", "07:00", "06:00", "07:00"],
        "temp_c": [14.2, 15.1, 56.8, None],
    })


def write_vectors(folder: Path) -> None:
    """How the files in tests/data/datahash/ were made."""
    readings = readings_frame()
    readings.write_parquet(folder / "readings-polars.parquet")
    pq.write_table(readings.to_arrow(), folder / "readings-pyarrow.parquet")
    readings.to_pandas().to_parquet(folder / "readings-pandas.parquet")
    pq.write_table(every_type(), folder / "every-type-pyarrow.parquet", row_group_size=3)
    pq.write_table(every_type().slice(0, 0), folder / "empty-pyarrow.parquet")


def hash_of(table: pa.Table, path: Path, **options) -> str:
    pq.write_table(table, path, **options)
    return uvs.data_hash(path)


# --- The rules ----------------------------------------------------------------

@pytest.mark.parametrize("options", [{}, {"row_group_size": 1}, {"row_group_size": 3, "use_dictionary": False}])
def test_matches_the_rules_written_out_by_hand(tmp_path, options):
    path = tmp_path / "t.parquet"
    pq.write_table(every_type(), path, **options)
    assert uvs.data_hash(path) == reference_hash(path)


def test_pinned_files_keep_their_hashes():
    """Files written once, by three libraries, give the same hashes on every
    pyarrow version: the hash depends on the data, not on the reader."""
    assert sorted(p.name for p in VECTORS.iterdir()) == sorted(EXPECTED)
    for name, expected in EXPECTED.items():
        assert uvs.data_hash(VECTORS / name) == expected, name
        assert reference_hash(VECTORS / name) == expected, name


def test_libraries_installed_now_give_the_pinned_hashes(tmp_path):
    pytest.importorskip("pandas")
    write_vectors(tmp_path)
    for name, expected in EXPECTED.items():
        assert uvs.data_hash(tmp_path / name) == expected, name


def test_rules_page_shows_the_pinned_hash():
    page = (ROOT / "user_guide" / "reference" / "data-hash.md").read_text(encoding="utf-8")
    assert EXPECTED["readings-polars.parquet"] in page


def test_hash_format(tmp_path):
    value = hash_of(every_type(), tmp_path / "t.parquet")
    prefix, algorithm, digest = value.split(":")
    assert (prefix, algorithm, len(digest)) == ("datahash1", "sha256", 64)
    assert digest == digest.lower() and int(digest, 16) >= 0


# --- How the file was written does not count ---------------------------------

@pytest.mark.parametrize("options", [
    {"compression": "none"},
    {"compression": "zstd"},
    {"use_dictionary": False},
    {"row_group_size": 1},
    {"data_page_version": "2.0"},
    {"write_statistics": False},
    {"version": "1.0"},
])
def test_writer_options_do_not_count(tmp_path, options):
    table = every_type().drop_columns(["float16", "uint32", "uint64", "ts_ns_tz", "duration_ns", "time64_ns", "nothing"])
    plain = hash_of(table, tmp_path / "plain.parquet")
    assert hash_of(table, tmp_path / "other.parquet", **options) == plain


def test_storage_types_that_are_the_same_data(tmp_path):
    def same(a: pa.Array, b: pa.Array) -> None:
        assert hash_of(pa.table({"x": a}), tmp_path / "a.parquet") == hash_of(pa.table({"x": b}), tmp_path / "b.parquet")

    same(pa.array(["a", None, ""]), pa.array(["a", None, ""], pa.large_string()))
    same(pa.array(["a", None, "a"]), pa.array(["a", None, "a"]).dictionary_encode())
    same(pa.array([b"ab", None]), pa.array([b"ab", None], pa.binary(2)))
    same(pa.array([b"ab", None]), pa.array([b"ab", None], pa.large_binary()))
    same(pa.array([1, None], pa.timestamp("s")), pa.array([1_000_000_000, None], pa.timestamp("ns")))
    same(pa.array([-1, None], pa.timestamp("ms", tz="UTC")), pa.array([-1000, None], pa.timestamp("us", tz="UTC")))
    same(pa.array([dt.date(2020, 1, 1)]), pa.array([dt.date(2020, 1, 1)], pa.date64()))
    same(pa.array([1, None], pa.time32("s")), pa.array([1_000_000, None], pa.time64("us")))
    same(pa.array([1, None], pa.duration("s")), pa.array([1_000_000_000, None], pa.duration("ns")))
    same(pa.array([decimal.Decimal("1.50")], pa.decimal128(5, 2)), pa.array([decimal.Decimal("1.50")], pa.decimal256(5, 2)))
    same(pa.array([-0.0, 0.0]), pa.array([0.0, 0.0]))
    same(floats([0x7FF8000000000000, 0xFFF8000000000001, 0x7FF0000000000001]), pa.array([math.nan] * 3))
    if hasattr(pa, "string_view"):
        view = tmp_path / "view.parquet"
        try:
            pq.write_table(pa.table({"x": pa.array(["a", None], pa.string_view())}), view)
        except (pa.ArrowException, NotImplementedError):
            pass  # this pyarrow cannot write string_view
        else:
            assert uvs.data_hash(view) == hash_of(pa.table({"x": pa.array(["a", None])}), tmp_path / "s.parquet")


def test_file_metadata_does_not_count(tmp_path):
    table = every_type()
    plain = hash_of(table, tmp_path / "plain.parquet")
    tagged = table.replace_schema_metadata({"written-by": "someone else"})
    assert hash_of(tagged, tmp_path / "tagged.parquet") == plain


# --- What counts --------------------------------------------------------------

@pytest.mark.parametrize("a, b", [
    ({"x": [1, 2]}, {"x": [1, 3]}),                    # a value
    ({"x": [1, 2]}, {"x": [1, None]}),                 # a value now missing
    ({"x": [1, 2]}, {"x": [2, 1]}),                    # row order
    ({"x": [1, 2]}, {"x": [1, 2, 2]}),                 # a row added
    ({"x": [1], "y": [2]}, {"y": [2], "x": [1]}),      # column order
    ({"x": [1]}, {"y": [1]}),                          # a column's name
    ({"x": ["", None]}, {"x": [None, ""]}),            # empty text is not missing
    ({"x": ["ab", "c"]}, {"x": ["a", "bc"]}),          # where one value ends
    ({"x": [0.1 + 0.2]}, {"x": [0.3]}),                # the last digit
])
def test_changes_that_count(tmp_path, a, b):
    assert hash_of(pa.table(a), tmp_path / "a.parquet") != hash_of(pa.table(b), tmp_path / "b.parquet")


@pytest.mark.parametrize("a, b", [
    (pa.array([1], pa.int32()), pa.array([1], pa.int64())),
    (pa.array([1], pa.int64()), pa.array([1], pa.uint64())),
    (pa.array([1.5], pa.float32()), pa.array([1.5], pa.float64())),
    (pa.array([1], pa.timestamp("s")), pa.array([1], pa.timestamp("s", tz="UTC"))),
    (pa.array([1], pa.timestamp("s", tz="UTC")), pa.array([1], pa.timestamp("s", tz="Europe/Paris"))),
    (pa.array([decimal.Decimal("1.50")], pa.decimal128(5, 2)), pa.array([decimal.Decimal("1.500")], pa.decimal128(5, 3))),
    (pa.array(["a"]), pa.array([b"a"])),
    (pa.array([None], pa.int64()), pa.nulls(1)),
])
def test_types_that_count(tmp_path, a, b):
    assert hash_of(pa.table({"x": a}), tmp_path / "a.parquet") != hash_of(pa.table({"x": b}), tmp_path / "b.parquet")


def test_every_column_counts_including_a_pandas_index(tmp_path):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"x": [1, 2, 3]}, index=[10, 20, 30])
    frame.to_parquet(tmp_path / "with.parquet", index=True)
    frame.to_parquet(tmp_path / "without.parquet", index=False)
    assert "__index_level_0__" in pq.ParquetFile(tmp_path / "with.parquet").schema_arrow.names
    assert uvs.data_hash(tmp_path / "with.parquet") != uvs.data_hash(tmp_path / "without.parquet")
    assert uvs.data_hash(tmp_path / "with.parquet") == reference_hash(tmp_path / "with.parquet")
    # The default 0, 1, 2, ... index is not saved as a column.
    pd.DataFrame({"x": [1, 2, 3]}).to_parquet(tmp_path / "default.parquet")
    assert uvs.data_hash(tmp_path / "default.parquet") == uvs.data_hash(tmp_path / "without.parquet")


def test_duplicate_column_names(tmp_path):
    table = pa.Table.from_arrays([pa.array([1, 2]), pa.array(["a", "b"]), pa.array([3, None])], names=["x", "y", "x"])
    path = tmp_path / "t.parquet"
    pq.write_table(table, path, row_group_size=1)
    assert uvs.data_hash(path) == reference_hash(path)


# --- Not covered, and errors ----------------------------------------------------

@pytest.mark.parametrize("column", [
    pa.array([[1, 2], None]),
    pa.array([{"a": 1}, None]),
    pa.array([[("k", 1)], None], pa.map_(pa.string(), pa.int64())),
])
def test_nested_columns_have_no_data_hash(tmp_path, column):
    path = tmp_path / "t.parquet"
    pq.write_table(pa.table({"flat": [1, 2], "nested": column}), path)
    with pytest.raises(uvs.DataHashError, match="column 'nested'") as caught:
        uvs.data_hash(path)
    assert caught.value.column == "nested"
    assert caught.value.to_dict()["path"] == str(path)


def test_a_file_that_is_not_parquet(tmp_path):
    path = tmp_path / "t.parquet"
    path.write_text("station,temp\nA,1\n")
    with pytest.raises(uvs.DataHashError, match="could not be read as a parquet file") as caught:
        uvs.data_hash(path)
    assert caught.value.column is None
    with pytest.raises(uvs.DataHashError):
        uvs.data_hash(tmp_path / "missing.parquet")


def test_every_covered_type_has_a_name():
    for field in every_type().schema:
        assert canonical_type(field.type) == kind(field.type), field.name


@pytest.mark.parametrize("name", [f.name for f in every_type().schema])
def test_a_chunk_that_starts_partway_encodes_the_same(name):
    """Arrays sliced from a larger one start partway into their buffers."""
    column = every_type().column(name).combine_chunks()
    kind_name = canonical_type(column.type)
    sliced = column.slice(1, 2)
    fresh = pa.concat_arrays([pa.array([], column.type), sliced])  # copied, so it starts at 0
    assert bytes(_encode(sliced, kind_name)) == bytes(_encode(fresh, kind_name))

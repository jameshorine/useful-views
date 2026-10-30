---
title: "Data hash rules"
---

useful-views calls a short code computed from something's contents a
**fingerprint**. A view can have two: a **file hash**, computed from the
file's bytes, and a **data hash**, computed from its *contents*, its columns
and its values. useful-views records the data hash (*new in version
0.2.0*); the file hash is planned. This page sets the rules for the data
hash. Unlike the file hash, it
does not depend on the library that wrote the file, its version, or how the
file is compressed and encoded. Two parquet files that hold the same table
have the same data hash. For why this matters, see
[Stale views and fingerprints](../concepts/stale-views.md) and
[Try it: two fingerprints](../guides/fingerprints.md).

`data_hash` computes it for any parquet file:

```python
import useful_views as uvs

uvs.data_hash("useful-views/gdp-per-capita.parquet")
```

When a recipe runs, useful-views records the data hash of each view it
makes, and of each view it reads, in `datapackage.json` (see
[Fingerprints](../concepts/records.md#fingerprints)). These rules are
fixed, so that every version of useful-views, on every computer, and any
other program that follows them, computes the same hash for the same table.

## In short

- Every column in the file counts, in the order the file stores them, with
  its name and type. No column is special.
- Every row counts, in the order the file stores them.
- Values count exactly. The only exceptions: every NaN is the same NaN, and
  `-0.0` is `0.0`.
- Text is text, however a library stores it (`string`, `large_string`,
  `string_view`). Number types stay distinct: an `int32` column is not an
  `int64` column, and `float32` is not `float64`.
- The units and storage widths of dates, times and decimals do not count: a
  timestamp stored in milliseconds and the same instant in nanoseconds are
  equal, as are `date32` and `date64`, and `decimal128` and `decimal256`
  with the same precision and scale.
- Whether a timestamp has a time zone counts (an instant, or a local
  wall-clock time), but not which zone: a time zone only says how to show
  the same instants, and not every library keeps its name in the file.
- How the file was written does not count: the library and its version,
  compression, encodings, row groups, dictionary encoding, statistics and
  metadata.
- Version 1 covers flat tables. A file with a nested column (a list, a struct
  or a map in each cell), a column it labels GEOMETRY or GEOGRAPHY, or any
  other type the table below does not cover has no data hash, and
  `data_hash` raises a `DataHashError` naming the column.

## Format

A data hash is written as

```text
datahash1:sha256:<64 lowercase hexadecimal digits>
```

`datahash1` names these rules. If the rules ever change, the new rules get a
new name (`datahash2`), and hashes made under different rules are never
compared.

## The byte stream

All numbers are written little-endian. "Length-prefixed" means: the length in
bytes as an unsigned 64-bit integer, then the bytes.

Each column is hashed on its own first. A column's **digest** is the SHA-256
of its rows, one after another, in file order, each encoded as described
below. The data hash is then the SHA-256 of:

1. The ASCII bytes `datahash1`, then one newline byte (`0x0A`).
2. The number of rows, as the file's metadata records it, as an unsigned
   64-bit integer.
3. The number of columns, as an unsigned 64-bit integer.
4. For each column, in file order (columns are taken by position, so two
   may share a name): its name (UTF-8, length-prefixed), its
   canonical type name (ASCII, length-prefixed; see below), and its digest
   (32 bytes).

Hashing each column on its own means a large view can be read one column
at a time, and a later version of useful-views could say which columns
changed.

Each row starts with one byte: `0x00` if the value is missing (null), `0x01`
if it is present. A missing value has nothing after that byte. A present
value is followed by its encoding.

## Types and values

A column's type is the Arrow type pyarrow reads from the file, which follows
the Arrow schema the writer saved in the file's metadata (`ARROW:schema`)
when there is one. It is reduced to a canonical type name, and the value is
encoded by the canonical type. One exception: a column the file itself
labels JSON or UUID (its Parquet logical type) is text or bytes, and one it
labels GEOMETRY or GEOGRAPHY is not covered, whatever type a version of
pyarrow reads it as.

| Types in the file | Canonical name | A present value is encoded as |
|---|---|---|
| `null`: the Arrow null type (a column of another type whose values are all missing keeps its type) | `null` | (every value is missing) |
| `bool` | `bool` | one byte: `0x00` false, `0x01` true |
| `int8`, `int16`, `int32`, `int64` | the same name | signed 64-bit integer |
| `uint8`, `uint16`, `uint32`, `uint64` | the same name | unsigned 64-bit integer |
| `float16`, `float32`, `float64` (pyarrow calls them `halffloat`, `float` and `double`) | `float16`, `float32`, `float64` | the value converted exactly to a 64-bit float (IEEE 754 binary64), as 8 bytes; every NaN is written with the bit pattern `0x7FF8000000000000` (bytes `00 00 00 00 00 00 F8 7F`), and `-0.0` as `0.0` |
| `decimal32(p, s)`, `decimal64(p, s)`, `decimal128(p, s)`, `decimal256(p, s)` | `decimal(p,s)`, with p and s in digits and no spaces, such as `decimal(9,3)` | the unscaled integer (the value times 10<sup>s</sup>), as a signed 256-bit integer: 32 bytes, little-endian two's complement |
| `string`, `large_string`, `string_view`, and a column labelled JSON | `string` | UTF-8, length-prefixed |
| `binary`, `large_binary`, `binary_view`, `fixed_size_binary(n)`, and a column labelled UUID | `binary` | the bytes, length-prefixed |
| `date32`, `date64` | `date` | days since 1970-01-01, as a signed 64-bit integer (for `date64`, its milliseconds divided by 86,400,000, rounded down) |
| `timestamp(unit)` | `timestamp` | the instant as whole seconds since 1970-01-01T00:00:00 (signed 64-bit) and the nanoseconds within that second (unsigned 32-bit, 0 to 999,999,999) |
| `timestamp(unit, tz)`, whatever the time zone | `timestamp[UTC]` | as above; the stored instant is already in UTC |
| `time32(unit)`, `time64(unit)` | `time` | nanoseconds since midnight, as a signed 64-bit integer |
| `duration(unit)` | `duration` | whole seconds (signed 64-bit) and nanoseconds within the second (unsigned 32-bit) |
| `dictionary(index, value)` | the canonical name of `value` | the value it stands for (missing when its index is missing) |
| anything else: lists, structs, maps, unions, intervals, columns the file labels GEOMETRY or GEOGRAPHY, and extension types other than JSON and UUID (whether or not the program reading the file knows the type: the file names it) | not covered | the file has no data hash in version 1 |

For timestamps and durations, seconds and nanoseconds are found by floor
division, so an instant before 1970 has negative seconds and a non-negative nanosecond part. A timestamp
stored in milliseconds and the same instant stored in microseconds or
nanoseconds therefore encode the same way.

## Not part of the hash

- the file's bytes, compression, encodings and page layout;
- row groups and how the table is split into batches;
- dictionary encoding, and whether a column is marked nullable;
- column statistics, and schema and column metadata, including the library
  that wrote the file and pandas' own metadata, except the Arrow schema
  saved there, which says which Arrow type to read each column as.

So a few things a writer chooses do change a column's type, and with it the
hash, because the file then holds a different type:

- Without the Arrow schema in its metadata (DuckDB and Spark do not save
  one), a duration column reads as `int64`.
- Parquet format version 1.0 stores a `uint32` column as `int64`.
- INT96 timestamps, from older Spark and Impala files, are read as
  nanoseconds with no time zone, so a column of instants hashes as
  `timestamp`, not `timestamp[UTC]`, and instants before 1677 or after 2262
  wrap around.

pandas saves its row index as an extra column, named like
`__index_level_0__`, unless it is a range of evenly spaced integers (the
default 0, 1, 2, …, or a slice of it), which pandas keeps only in its
metadata, so that it does not count. A saved index column is in the file,
so it counts, like any other. To leave it out, write with `index=False`.

A data hash says whether two files hold the same table, not whether every
program that reads them sees the same thing: a step can see a time zone's
name, a column's storage type or unit (`large_string`, milliseconds), or its
categories, none of which count. So `sync` never takes a data hash that
stayed the same as a reason to skip a recipe (see [Stale views and fingerprints](../concepts/stale-views.md)).

## Speed

![How long data_hash takes, by the view's size and by column type](data-hash-speed.svg)

The figure shows `data_hash` on one computer, an Apple M2 laptop, named
under the figure.

- **Time** (left), for views of growing size with a mix of integer,
  float, text and timestamp columns. The lines are straight and evenly
  spaced: the time grows in step with the number of values, rows times
  columns, at about 22 million values a second. A view of 10 million rows
  and 4 columns takes about 2 seconds; one of 10 million rows and 256
  columns, 2.56 billion values and 15 GB on disk, takes 2 minutes. The dashed
  lines are the time to read the same file a column at a time, as
  `data_hash` does. Hashing takes about 9 times as long, because every value
  is encoded.
- **Column types** (right). Text and timestamps take about twice as long
  per value as integers and floats.

**Memory.** `data_hash` reads each column in batches of 65,536 rows, a row
group at a time (small row groups a few together, up to 65,536 rows), so it
never holds the whole table. Its peak memory stays
level as views grow: between about 120 and 200 MB for every view of a
million rows or more, up to the 15 GB one, of which about 100 MB is Python
with pyarrow loaded. How the file was written matters only a little:
pyarrow loads the compressed bytes of one column of one row group at once,
so memory grows with the largest row group, not with the file. A file
written as a single row group of 10 million rows needed 270 MB.

The figure is made by `scripts/benchmark_data_hash.py` in the useful-views
repository. It measures memory too, without drawing it, checks at the end
that memory stayed level, and saves every timing and peak memory, with the
computer and versions, in [data-hash-speed.json](data-hash-speed.json).

To time it on your computer:

```python
import time

import polars as pl
import useful_views as uvs

n = 5_000_000
frame = pl.select(
    id=pl.int_range(n),
    x=pl.int_range(n) / 7,
    name=pl.format("station-{}", pl.int_range(n) % 997),
    when=pl.datetime_range(pl.datetime(2020, 1, 1), pl.datetime(2020, 1, 1) + pl.duration(seconds=n - 1), "1s"),
)
frame.write_parquet("big.parquet")

start = time.perf_counter()
uvs.data_hash("big.parquet")
print(f"{time.perf_counter() - start:.1f} seconds")
```

## Test vectors

The sensor readings from [Try it: two fingerprints](../guides/fingerprints.md),
with `station` and `time` as text and `temp_c` as a 64-bit float:

| station | time | temp_c |
|---|---|---|
| A | 06:00 | 14.2 |
| A | 07:00 | 15.1 |
| B | 06:00 | 56.8 |
| B | 07:00 | (missing) |

have the data hash

```text
datahash1:sha256:76c92a8bef6fae8ffaffcd6a3558af6146eee1f608168632539e42accdebbc18
```

whether polars, pyarrow or pandas wrote the file. The useful-views
repository keeps this table in three files, one from each library, and
three more:

| File | Data hash |
|---|---|
| `every-type-pyarrow.parquet`: the covered types (one storage type each where there are several), with missing values and edge cases | `datahash1:sha256:c866a136d9d23368e90f8b4210bd2f63586a8cd1142f048356e227c92a4f4817` |
| `empty-pyarrow.parquet`: the same columns, no rows | `datahash1:sha256:7170a8822d57581c3d22fbac4ac610df67ae3a6404dd22910f9112135f2d82df` |
| `json-uuid-pyarrow.parquet`: columns labelled JSON and UUID | `datahash1:sha256:d165d177de794e6b515fbd9355da403ccebbb3f9d108bfee0461a53e38407e2f` |

They are in `tests/data/datahash/`, with their hashes in
`tests/test_data_hash.py`. The tests check them with the newest pyarrow on
Linux, macOS and Windows, and with the oldest supported, pyarrow 16, on
Linux, against `data_hash` and against a second, plain implementation
written straight from this page. To implement these rules in another
language, start from those files.

pyarrow 16 cannot open every file newer pyarrow writes: it refuses 32- and
64-bit decimals. Such a file has a data hash, the same under any pyarrow
that can read it, but pyarrow 16 cannot compute it.

## Promises

- The hash these rules give a file never changes. A rule that would change
  any hash gets a new name (`datahash2`).
- Nested columns will be covered by adding to version 1: every file that
  has a data hash today keeps it, and files that have none only because of
  a nested column gain one.

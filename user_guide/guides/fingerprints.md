---
title: "Try it: two fingerprints"
---

[Stale views and fingerprints](../concepts/stale-views.md) explains the two
fingerprints a view can have: a **file hash** and a **data hash**.
useful-views records the data hash of every view it makes; the file hash
is planned. This guide computes both by hand, in a few lines
of Python, so you can see for yourself what each one notices and why one
alone is not enough. At the end, you check the result against useful-views'
own data hash.

You need polars and pyarrow, which the [quickstart](../quickstart.md) setup
installs. Work in a new, empty folder, and run each block in order, in one
Python session or notebook.

## Two fingerprints

```python
import hashlib

import polars as pl
import pyarrow.parquet as pq


def file_hash(path):
    """SHA-256 of the file's bytes."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def data_hash(path):
    """SHA-256 of the table's contents: the column names and types, then
    every value, whatever library wrote the file."""
    table = pq.read_table(path)
    h = hashlib.sha256()
    for field in table.schema:
        # Libraries store text as string, large_string or string_view: the same data.
        kind = "string" if "string" in str(field.type) else str(field.type)
        h.update(f"{field.name}:{kind};".encode())
    for column in table.columns:
        for value in column.to_pylist():
            h.update(repr(value).encode() + b";")
    return h.hexdigest()
```

`data_hash` reads the whole table and goes value by value, which is fine for
a small example. A real one works a column at a time and is much faster.

## The readings

Temperatures from two field sensors. One reading, station B at 06:00, was
recorded in degrees Fahrenheit by mistake: 56.8 °F is 13.8 °C.

```python
readings = pl.DataFrame({
    "station": ["A", "A", "B", "B"],
    "time": ["06:00", "07:00", "06:00", "07:00"],
    "temp_c": [14.2, 15.1, 56.8, None],
})
readings.write_parquet("readings.parquet")
```

## 1. The same data, saved by two libraries

Save the same table again, this time with pyarrow, as a step written with
another library would.

```python
pq.write_table(readings.to_arrow(), "readings_pyarrow.parquet")
print("file hash equal:", file_hash("readings.parquet") == file_hash("readings_pyarrow.parquet"))
print("data hash equal:", data_hash("readings.parquet") == data_hash("readings_pyarrow.parquet"))
```

```text
file hash equal: False
data hash equal: True
```

The data is identical, but the bytes are not: each library writes its own
name into the file, and encodes and compresses the columns its own way. A
library upgrade can do the same. So a file hash recorded on your computer
can differ from a co-author's for the very same results, while the data hash
matches.

## 2. The same library, saved twice

```python
readings.write_parquet("readings_again.parquet")
print("file hash equal:", file_hash("readings.parquet") == file_hash("readings_again.parquet"))
print("data hash equal:", data_hash("readings.parquet") == data_hash("readings_again.parquet"))
```

```text
file hash equal: True
data hash equal: True
```

On one computer, with the same library, the file hash is reliable, and it is
much cheaper: it needs no understanding of the file at all. That makes it the
right check for "is this still the file that was recorded?"

## 3. A real change: fixing the unit error

```python
fixed = readings.with_columns(
    pl.when((pl.col("station") == "B") & (pl.col("time") == "06:00"))
    .then(((pl.col("temp_c") - 32) * 5 / 9).round(1))
    .otherwise(pl.col("temp_c"))
    .alias("temp_c")
)
fixed.write_parquet("readings_fixed.parquet")
print("file hash equal:", file_hash("readings.parquet") == file_hash("readings_fixed.parquet"))
print("data hash equal:", data_hash("readings.parquet") == data_hash("readings_fixed.parquet"))
```

```text
file hash equal: False
data hash equal: False
```

One value changed, and both fingerprints changed. Either one
would tell useful-views that a view made from the old readings is now stale.

## 4. The same rows, in another order

```python
fixed.reverse().write_parquet("readings_reversed.parquet")
print("data hash equal:", data_hash("readings_fixed.parquet") == data_hash("readings_reversed.parquet"))
```

```text
data hash equal: False
```

Same rows, other order, different data hash. Many operations return rows in
an order that can change from one run to the next, such as `group_by` in
polars (unless you pass `maintain_order=True`) and some joins. A step that
sorts its result before writing it, for example with
`.sort("station", "time")`, gives the same fingerprint every time.

## 5. The last digit of a number

Add the same three numbers in two orders, as a computation split across
several threads might:

```python
first = (0.1 + 0.2) + 0.3
second = 0.1 + (0.2 + 0.3)
print(first, second)
pl.DataFrame({"total": [first]}).write_parquet("total_a.parquet")
pl.DataFrame({"total": [second]}).write_parquet("total_b.parquet")
print("data hash equal:", data_hash("total_a.parquet") == data_hash("total_b.parquet"))
```

```text
0.6000000000000001 0.6
data hash equal: False
```

Shown to a few decimal places, both are 0.6. Fingerprints are exact, so they
count the difference in the 16th digit. That is honest: the two results are
not identical. Deciding that two numbers are close enough is a comparison
with a tolerance, which is a separate question from whether anything changed.

## 6. useful-views' own data hash

The `data_hash` above is a sketch. useful-views has the real one,
`uvs.data_hash()`. It follows exact, versioned
[rules](../reference/data-hash.md), and it reads a large file a column at a
time:

```python
import useful_views as uvs

print(uvs.data_hash("readings.parquet"))
print(uvs.data_hash("readings.parquet") == uvs.data_hash("readings_pyarrow.parquet"))
```

```text
datahash1:sha256:76c92a8bef6fae8ffaffcd6a3558af6146eee1f608168632539e42accdebbc18
True
```

Your first line should match this one character for character. The data
hash depends only on the readings, so it is the same on every computer,
whichever library wrote the file.

## What each fingerprint notices

| What happened | File hash | Data hash |
|---|---|---|
| saved by another library, or a newer version of one | usually changes | **same** |
| saved again by the same library | same | same |
| a value corrected | changes | changes |
| the same rows in another order | changes | changes |
| a number differs in its last digit | changes | changes |

The two answer different questions. The file hash asks "is this the file
that was recorded?", and is the cheap, exact check on one computer. The data
hash asks "is this the same data?", and is the one that works across
computers, co-authors and library versions. The data hash is what
useful-views' [stale-view checks](../concepts/stale-views.md) are built on.

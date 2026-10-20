"""Time data_hash on views of growing size, and draw the figure in the docs.

    uv run --group bench python scripts/benchmark_data_hash.py

Run it by hand, on one computer, plugged in and with little else running,
when the hashing code changes. The largest view is 10 million rows by 256
columns (about 20 GB on disk, written to a temporary folder and deleted
straight after), so a run takes a while. It writes, next to the "Data hash
rules" page:

- user_guide/reference/data-hash-speed.json: the timings and peak memory,
  with the computer and versions, saved after every view;
- user_guide/reference/data-hash-speed.svg: the figure on that page.

To redraw the figure from the saved timings: add --plot-only. Timings depend
on the computer, so CI never runs this; tests/test_data_hash.py only checks
that it still works, at a tiny size.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import polars as pl
import pyarrow
import pyarrow.parquet as pq

import useful_views as uvs

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "user_guide" / "reference"
RESULTS = DOCS / "data-hash-speed.json"
FIGURE = DOCS / "data-hash-speed.svg"

ROWS = [10_000, 30_000, 100_000, 300_000, 1_000_000, 3_000_000, 10_000_000]
# Each count is 4 times the last, so the lines are evenly spaced on the
# figure's log scale, and every mixed view has as many columns of each type.
COLUMNS = [1, 4, 16, 64, 256]
TYPES = ["integer", "float", "text", "timestamp"]
# The by-type panel: one view per type, this many rows and columns.
TYPE_SIZE = (5_000_000, 4)
# Views are written this many rows at a time, one row group each, so even
# the largest is never in memory whole.
CHUNK_ROWS = 1_000_000
REPEATS = 3
# A timing longer than this is a single run; shorter ones are the median of
# REPEATS runs after a warm-up.
LONG_SECONDS = 10.0

# Runs in a fresh Python for each measurement, so the peak memory it reports
# belongs to that measurement alone.
CHILD = r"""
import json, statistics, sys, time
import pyarrow.parquet as pq
import useful_views as uvs

what, path, repeats, long = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])

def once():
    start = time.perf_counter()
    if what == "hash":
        uvs.data_hash(path)
    elif what == "read":
        # Every column of every row group, one at a time, as data_hash reads them.
        f = pq.ParquetFile(path)
        for group in range(f.num_row_groups):
            for name in f.schema_arrow.names:
                f.read_row_group(group, columns=[name])
    return time.perf_counter() - start

times = [once()]
if times[0] <= long:  # quick: that was the warm-up
    times = [once() for _ in range(repeats)]
try:
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = peak if sys.platform == "darwin" else peak * 1024  # bytes on macOS, KB on Linux
except ImportError:  # Windows
    peak = None
print(json.dumps({"seconds": statistics.median(times), "runs": len(times), "peak_bytes": peak}))
"""


def frame(start: int, stop: int, kinds: list[str]) -> pl.DataFrame:
    """Rows start to stop of a view with one column of each kind given.

    The values look random, as real measurements do; numbers counting up
    would compress unrealistically well. A row always gets the same values.
    """
    columns = {}
    for j, kind in enumerate(kinds):
        r = pl.int_range(start, stop, dtype=pl.Int64).hash(seed=j)
        if kind == "integer":  # below a million, like counts or IDs
            column = (r % 1_000_000).cast(pl.Int64)
        elif kind == "float":  # 0 to 100, with 15 significant digits
            column = (r % 10**15).cast(pl.Float64) / 10**13
        elif kind == "text":  # about 12 characters
            column = pl.format("station-{}", r % 997)
        else:  # some time in five years
            column = pl.datetime(2020, 1, 1) + pl.duration(seconds=(r % 157_680_000).cast(pl.Int64))
        columns[f"{kind}_{j}"] = column
    return pl.select(**columns)


def mixed(columns: int) -> list[str]:
    return [TYPES[j % len(TYPES)] for j in range(columns)]


def write_view(path: Path, rows: int, kinds: list[str], chunk_rows: int) -> None:
    writer = None
    try:
        for start in range(0, rows, chunk_rows):
            piece = frame(start, min(rows, start + chunk_rows), kinds).to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(path, piece.schema)
            writer.write_table(piece, row_group_size=chunk_rows)
    finally:
        if writer is not None:
            writer.close()


def child(what: str, path: Path, repeats: int) -> dict:
    done = subprocess.run(
        [sys.executable, "-c", CHILD, what, str(path), str(repeats), str(LONG_SECONDS)],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(f"measuring {what} on {path} failed:\n{done.stderr}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _mb(peak_bytes: int | None) -> float | None:
    return None if peak_bytes is None else round(peak_bytes / 1e6, 1)


def measure(folder: Path, rows: int, kinds: list[str], repeats: int, chunk_rows: int) -> dict:
    path = folder / "view.parquet"
    try:
        write_view(path, rows, kinds, chunk_rows)
        hashed = child("hash", path, repeats)
        read = child("read", path, repeats)
        return {
            "rows": rows,
            "columns": len(kinds),
            "values": rows * len(kinds),
            "megabytes": round(path.stat().st_size / 1e6, 2),
            "hash_seconds": hashed["seconds"],
            "hash_runs": hashed["runs"],
            "hash_peak_mb": _mb(hashed["peak_bytes"]),
            "read_seconds": read["seconds"],
            "read_runs": read["runs"],
            "read_peak_mb": _mb(read["peak_bytes"]),
        }
    finally:
        path.unlink(missing_ok=True)


def has_room(folder: Path, values: int) -> bool:
    """Room for the view (at most about 9 bytes a value) and 5 GB to spare."""
    return shutil.disk_usage(folder).free > values * 9 + 5e9


def computer() -> dict:
    """What the timings were measured on."""
    processor = platform.processor() or platform.machine()
    system = f"{platform.system()} {platform.release()}"
    if sys.platform == "darwin":
        processor = _command(["sysctl", "-n", "machdep.cpu.brand_string"]) or processor
        system = f"macOS {platform.mac_ver()[0]}"
    elif sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    processor = line.split(":", 1)[1].strip()
                    break
        except OSError:
            pass
    try:
        memory = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
    except (AttributeError, ValueError, OSError):
        memory = None
    # "-dirty" when the code measured has changes not yet committed.
    commit = _command(["git", "-C", str(ROOT), "describe", "--always", "--dirty", "--abbrev=7"])
    return {
        "processor": processor,
        "cores": os.cpu_count(),
        "memory_gb": memory,
        "system": system,
        "python": platform.python_version(),
        "pyarrow": pyarrow.__version__,
        "polars": pl.__version__,
        "useful_views": uvs.__version__ + (f" ({commit})" if commit else ""),
        "date": dt.date.today().isoformat(),
    }


def _command(args: list[str]) -> str | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def run(rows=ROWS, columns=COLUMNS, type_size=TYPE_SIZE, repeats=REPEATS, chunk_rows=CHUNK_ROWS,
        show=print, save=None) -> dict:
    """Measure every size, then every type. `save(results)` is called after each view."""
    results = {
        "computer": computer(),
        "repeats": repeats,
        "long_seconds": LONG_SECONDS,
        "chunk_rows": chunk_rows,
        "baseline_peak_mb": None,
        "sizes": [],
        "types": [],
    }
    started = time.perf_counter()

    def done(entry: dict, label: str) -> None:
        memory = f", peak memory {entry['hash_peak_mb']:,.0f} MB" if entry["hash_peak_mb"] else ""
        minutes = (time.perf_counter() - started) / 60
        show(f"[{minutes:5.1f} min] {label}: {entry['hash_seconds']:8.3f} s{memory}"
             f"  (reading: {entry['read_seconds']:.3f} s)")
        if save:
            save(results)

    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        results["baseline_peak_mb"] = _mb(child("nothing", folder / "none", 1)["peak_bytes"])
        for n in rows:
            for p in columns:
                label = f"{n:>10,} rows × {p:>3} columns"
                if not has_room(folder, n * p):
                    show(f"skipped {label.strip()}: not enough free disk space")
                    continue
                results["sizes"].append(measure(folder, n, mixed(p), repeats, chunk_rows))
                done(results["sizes"][-1], label)
        n, p = type_size
        for kind in TYPES:
            results["types"].append({"type": kind, **measure(folder, n, [kind] * p, repeats, chunk_rows)})
            done(results["types"][-1], f"{kind:>10}, {n:,} rows × {p} columns")
    return results


def caption(results: dict) -> str:
    machine = results["computer"]
    memory = f", {machine['memory_gb']} GB" if machine.get("memory_gb") else ""
    text = (
        f"Measured {machine['date']} on {machine['processor']} ({machine['cores']} cores{memory}), "
        f"{machine['system']}; Python {machine['python']}, pyarrow {machine['pyarrow']}, "
        f"useful-views {machine['useful_views']}. Each time is the median of {results['repeats']} runs "
        f"after a warm-up, or a single run when it took over {results.get('long_seconds', 0):g} seconds. "
        f"Files written by pyarrow in row groups of {results.get('chunk_rows', 0):,} rows."
    )
    return textwrap.fill(text, 175)


def plot(results: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "useful-views", "font.size": 10})
    figure = plt.figure(figsize=(10, 7.8), facecolor="white")
    grid = figure.add_gridspec(2, 2, height_ratios=[3, 1.5], width_ratios=[3, 2])
    timing, memory, speed = figure.add_subplot(grid[0, 0]), figure.add_subplot(grid[0, 1]), figure.add_subplot(grid[1, :])

    sizes = results["sizes"]
    counts = sorted({r["columns"] for r in sizes})
    for k, p in enumerate(counts):
        points = sorted((r for r in sizes if r["columns"] == p), key=lambda r: r["rows"])
        x = [r["rows"] for r in points]
        label = f"{p} columns" if p > 1 else f"1 column ({TYPES[0]})"
        timing.plot(x, [r["hash_seconds"] for r in points], marker="o", markersize=4, color=f"C{k}", label=label)
        timing.plot(x, [r["read_seconds"] for r in points], linestyle="--", linewidth=1, color=f"C{k}", alpha=0.4)
        peaks = [r.get("hash_peak_mb") for r in points]
        if all(peaks):
            memory.plot(x, peaks, marker="o", markersize=4, color=f"C{k}")
    for axes in (timing, memory):
        axes.set_xscale("log")
        axes.xaxis.set_major_formatter(FuncFormatter(_count))
        axes.set_xlabel("Rows")
        axes.grid(True, which="major", color="0.92")
    timing.set_yscale("log")
    timing.yaxis.set_major_formatter(FuncFormatter(lambda s, _: f"{s:g} s"))
    timing.set_title("Time to hash a view, by size", loc="left")
    handles, _ = timing.get_legend_handles_labels()
    handles.append(Line2D([], [], linestyle="--", linewidth=1, color="0.5", label="reading the file only"))
    timing.legend(handles=handles, title="Mixed column types", loc="upper left", frameon=False, fontsize=9,
                  title_fontsize=9)

    memory.set_title("Peak memory while hashing", loc="left")
    memory.yaxis.set_major_formatter(FuncFormatter(lambda m, _: f"{m:,.0f} MB"))
    baseline = results.get("baseline_peak_mb")
    if baseline:
        memory.axhline(baseline, linestyle=":", color="0.4", linewidth=1)
        memory.annotate("Python and pyarrow loaded,\nnothing hashed", (memory.get_xlim()[0], baseline),
                        xytext=(4, -24), textcoords="offset points", fontsize=8, color="0.35")
    if not memory.lines:
        memory.text(0.5, 0.5, "not measured on this system", ha="center", transform=memory.transAxes, color="0.4")
    memory.set_ylim(bottom=0)

    types = results["types"]
    speeds = [r["values"] / r["hash_seconds"] / 1e6 for r in types]
    bars = speed.barh([r["type"] for r in types], speeds, color="C0", height=0.6)
    speed.invert_yaxis()
    speed.bar_label(bars, labels=[f"{s:.0f}" for s in speeds], padding=3, fontsize=9)
    n, p = types[0]["rows"], types[0]["columns"]
    speed.set_xlabel(f"Million values a second ({_count(n)} rows × {p} columns of one type)")
    speed.set_title("Speed by column type", loc="left")
    speed.set_xlim(0, max(speeds) * 1.12)
    for axes in (timing, memory, speed):
        axes.spines[["top", "right"]].set_visible(False)

    figure.text(0.01, 0.012, caption(results), fontsize=7.5, color="0.35", va="bottom")
    figure.tight_layout(rect=(0, 0.06, 1, 1))
    figure.savefig(path, format="svg", metadata={"Date": None})
    plt.close(figure)


def _count(n, _=None) -> str:
    for size, suffix in ((1e6, "M"), (1e3, "k")):
        if n >= size:
            return f"{n / size:g}{suffix}"
    return f"{n:g}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plot-only", action="store_true", help="redraw the figure from the saved timings")
    args = parser.parse_args()
    if args.plot_only:
        results = json.loads(RESULTS.read_text(encoding="utf-8"))
    else:
        def save(partial: dict) -> None:
            RESULTS.write_text(json.dumps(partial, indent=2) + "\n", encoding="utf-8")

        start = time.perf_counter()
        results = run(save=save)
        print(f"wrote {RESULTS.relative_to(ROOT)} in {(time.perf_counter() - start) / 60:.1f} minutes")
    plot(results, FIGURE)
    print(f"wrote {FIGURE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

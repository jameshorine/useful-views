# Changelog

All notable changes to useful-views are recorded here. Versions follow
[semantic versioning](https://semver.org/); while the version starts with
0, a minor release may change behaviour.

## 0.3.0 (2026-11-20)

### Breaking changes

- `recipe.toml` now lists the recipe's steps. Each step is a `[[steps]]`
  block at the end of the file, with `file`, the step file's name, and
  `description`, one sentence on what the step does to the data. The blocks
  must list exactly the `NN_name.py` files in the folder, in number order.
  Rule 2 is now "Steps are numbered and listed". `new-recipe` writes one
  block, for `01_first_step.py`.
- Rule 1 now covers the `[[steps]]` blocks. A recipe with no `[[steps]]`,
  or with a step description that is missing or starts with TODO, is
  refused under rule 1 (it was rule 2). `RecipeRefused.rule` and the `rule`
  in `--json` output change to match.
- A step file's top comment is no longer its description, and its comments
  aren't read. useful-views only checks that a step file can be read as
  UTF-8 (rule 2). The records keep the same fields: each step's `name`,
  `path` and `description`.

### New features

- `sources.toml`, an optional file at the project root, says where each raw
  file comes from (#22, #23). It has one `[[sources]]` block per source:
  title, files, URL or DOI, access, cost, time, date obtained, license,
  citation, and whether its files are shared.
  - useful-views copies the details into each raw file's record in
    `datapackage.json` (Data Package `sources` and `licenses`). The catalog
    and the cookbook list them.
  - Editing `sources.toml` never makes a recipe out of date. `check`
    reports `stale-sources`, and `sync` writes the details without running
    anything. `cookbook --check` catches it in CI.
  - A `sources.toml` that can't be read is reported as
    `unreadable-sources`.
  - Once a project has one, raw files with no source get a note.

### Other changes

- The `pwt` example uses the new format and has a `sources.toml`. `example`
  downloads tag `examples-v3` of useful-views-data.
- The docs are reorganised and rewritten (#31). There's a short README, a
  Get started walkthrough, one home page for each topic, and examples that
  show real output.

### Upgrading

- In each `recipe.toml`, add a `[[steps]]` block at the end for each step
  file, in order, with its old top comment as the description. Until you
  do, `run` and `sync` refuse the recipe, and `check` reports it with a
  block to paste for each step file.
- Copy the descriptions word for word. They then match the records, so
  `sync` has nothing to run.
- Put a description that runs over several lines, or has double quotes in
  it, in triple quotes (`"""..."""`). Put one with a backslash in triple
  single quotes (`'''...'''`), which keep it as written. See "Recipes" in
  the user guide.

## 0.2.0 (2026-10-30)

Fingerprints: useful-views now records what each view contains, and uses it
to find views made from older data.

- `data_hash(path)` computes a parquet file's data hash: a fingerprint of
  the table it holds, the same whichever library or version wrote the file.
  The rules are fixed and versioned (`datahash1`), on the new "Data hash
  rules" page, and pinned by test files written by pyarrow, polars and
  pandas. It reads each column in batches of 65,536 rows, one row group at a
  time, so its memory stays level however large the view: 120 to 200 MB up
  to a 15 GB view on an Apple M2 laptop, and 270 MB for a file written as a
  single row group of 10 million rows. It hashes about 22 million values a
  second. Time zones do not count, only the instants they show, and columns
  a file labels JSON or UUID hash the same on every pyarrow version. A file
  with a nested column, or a column it labels GEOMETRY or GEOGRAPHY, has no
  data hash in version 1; `DataHashError` names the column.
- When a recipe runs, `datapackage.json` records each view's fingerprint
  (its data hash, or `null` when a column is not covered) and, in
  `producedBy.inputFingerprints`, the fingerprints of the views the recipe
  read, taken just before its steps ran. So every `run` and `sync` reads
  each view a recipe makes or reads once more, and `run` also reads the file
  a view replaces when other recipes read it: seconds for most views, about
  2 minutes for a 15 GB one. A fingerprint this version does not understand
  (from a newer version of useful-views, or a hand edit) is never an error:
  it counts as unknown, and `check` and `sync` say so.
- `check` and `sync` use the fingerprints. A recipe whose record shows it
  read a view since made again with different data is `out-of-date`, with
  the new reason `input-data-changed`: `sync` runs it and what is
  downstream of it, and `cookbook --check` fails on it, without needing the
  data. A data hash that comes out the same is never a reason for `sync` to
  skip a recipe, since a step can see what the hash leaves out (a time
  zone's name, a column's storage type or unit, its categories): `sync`
  still runs every recipe downstream of one it runs. After `run`, a warning
  names the recipes that read its views: out of date when the data changed
  (`sync` runs them); when it came out the same, to run them too if what
  changed is how the data is stored; and when their records cannot show
  which, why, and to use `sync --all`. A recipe whose
  input view on disk is not the recorded one (left by a `git pull` or a
  branch switch) is not run: `run` and `sync` stop and say which recipe to
  run first. Recipes whose records cannot show whether they are out of date
  (recorded by another version, or reading a view with no data hash) get a
  note in `run`, `check` and `sync` saying why and what to do. Fingerprints made
  under different rules are never compared.
- `datapackage.json` is laid out to be read: recipes, and the views under
  them, in the order they run; each view's name, path, fingerprint and
  `producedBy` first and its columns last; short items on one line. Its
  content is unchanged, but the first `sync` or `run` after upgrading
  rewrites it, and the catalog, in the new order, even when nothing needs to
  run (`sync` says so). The catalog lists views in run order too.
- `datapackage.json` is checked more strictly: a view must be a `.parquet`
  file directly in `useful-views/`, no recorded path may hold control or
  text-direction characters, every field has the type useful-views writes,
  and no view, raw file or recipe is recorded twice. So a crafted record can
  neither point `sync` at other files nor print terminal escape codes, and a
  broken one is refused with how to restore it rather than a crash. Paths in
  `recipe.toml` may not hold those characters either. Error messages name
  files relative to the project. A field useful-views does not write,
  perhaps from a newer version, no longer makes a recipe out of date, and is
  kept when the file is rewritten, except in the entries a run writes
  afresh: those of the recipe that ran, its views and the raw files it read.
- New pages: "Stale views and fingerprints" (concepts), "Try it: two
  fingerprints" (guides) and "Data hash rules" (reference), with a figure of
  hashing time by the size of the view and by column type.
- For maintainers: `scripts/benchmark_data_hash.py` measures hashing time
  and peak memory, checks that memory stays level, and redraws that figure
  (`uv run --group bench python scripts/benchmark_data_hash.py`). Its
  plotting library, matplotlib, is in a new `bench` dependency group, so
  users never install it.

### Upgrading from 0.1

- Run `useful-views sync --all` once, to record every view's fingerprint,
  then `useful-views cookbook` if the project has a cookbook, and commit.
- Move any pinned version, such as one in a CI job running `cookbook
  --check`, to 0.2.0.
- Sort a view's rows by columns that together identify each row before
  writing it: rows in a different order on each run give the view a new
  fingerprint every run, so the recipes downstream of it look out of date.

### Not yet

- A view file on disk is compared with its record only when a recipe is
  about to read it; `check` does not look. So after a `git pull`,
  merge or branch switch, still use `useful-views sync --all`.
- A change to a step's code, a helper module or a raw file is noticed only
  once that recipe runs again.

## 0.1.0 (2026-10-15)

The first release: the minimum version.

- A fixed project structure: `raw/`, `recipes/`, `work/`, `useful-views/`,
  a generated `datapackage.json` and a generated catalog,
  `useful-views/README.md`.
- `init`, `new_recipe`, `run`, `check` and `sync`, as Python functions and
  as the `useful-views` command, and `root` (Python only). `init` also writes
  an `AGENTS.md` for AI coding assistants, and refuses the home folder or the
  top of a drive without `--force`.
- `example` downloads a ready-to-run example project from the
  useful-views-data repository, pinned to a tag and checked by SHA-256.
- `check` reports what `run` and `sync` would refuse (`refused`, with the
  rule number) and recipes changed since they last ran (`out-of-date`).
  `sync` checks every recipe before changing anything and lists every
  problem at once.
- `useful-views cookbook` writes `cookbook.json`, the ledger of the recipes a
  project uses, in run order (format version 1; file hashes are planned);
  `cookbook --check` verifies it in CI without needing the data, and `check`
  reports `stale-cookbook` (and `stale-catalog` for a hand-edited catalog).
- For scripts and AI tools: `--json` on every command (with `warnings` and one
  error shape), `-C <path>`, `useful-views rules` (and `useful_views.RULES`),
  exit codes in `--help`, and errors that carry the recipe, rule, file and
  step (`to_dict()`). Progress lines go to standard error.
- One command at a time: `run`, `sync` and `cookbook` hold a lock on the
  project. On the command line, SIGTERM and SIGHUP stop the running step and
  clean up as Ctrl-C does. `run` warns about recipes downstream of it, and a
  `sync` that stops partway removes the views it left out of date.
- Recipes are folders with a `recipe.toml` and numbered Python step files;
  every view records the recipe, inputs and steps that made it.
- `sync` makes the views match the recipe folders, like `uv sync`: it runs
  what is missing or changed, upstream first, and removes what is no longer
  declared.
- Schemas are read with pyarrow; the generated `datapackage.json` passes
  `frictionless validate`.
- A documentation site built with great-docs: getting started, concepts
  (projects, steps, recipes, running, records and the cookbook), a guide for
  AI tools, a generated command-line and rules reference, an API reference
  from the docstrings, and `llms.txt` and a hand-written `SKILL.md` for
  coding agents.
- Tested on Python 3.11 to 3.14.

# Instructions for AI coding assistants

This is a useful-views data project (https://jameshorine.github.io/useful-views/).
Read `README.md`, `recipes/*/recipe.toml` and `useful-views/README.md` first.

- `useful-views rules` lists the rules a recipe must follow.
  `useful-views check --json` lists every problem. Neither changes anything.
- Steps are code: `useful-views run` and `useful-views sync` run every step
  in `recipes/` with the user's permissions and environment variables. Ask
  the user before you run recipes you didn't write.
- Never edit `datapackage.json`, `useful-views/README.md` or `cookbook.json`:
  useful-views writes them. Never write files into `raw/` or `useful-views/`
  yourself: steps write the views.
- To add a recipe, run `useful-views new-recipe <name>`, then fill in its
  `recipe.toml` and steps. Paths start at the project root, use forward
  slashes, and are the same strings in `recipe.toml` and in the code.
- `recipe.toml` ends with a `[[steps]]` block for every step file
  (`NN_name.py`), in number order: `file` is the file's name, and
  `description` is one sentence on what the step does to the data. When you
  add, rename or remove a step file, change its block too. Save step files
  as UTF-8. Comments in them aren't read.
- When a recipe reads a new raw file, describe it in `sources.toml`: a
  `[[sources]]` block with at least `title` and `files`. Ask the user for its
  source, license and citation; don't guess. Then run `useful-views sync`.
- To build, run `useful-views sync`. Run one command at a time.
  - After editing a step's code in recipe X, run `useful-views run X`, then
    what its warnings say to run.
  - After a `git pull`, a branch switch, or a raw file replaced under the
    same name, run `useful-views sync --all`.
- A `check` finding with reason `input-data-changed` means the recipe was
  made from an older version of a view it reads; `useful-views sync` runs
  it. Steps should sort the rows they write by columns that together
  identify each row, or every run looks like a change.
- In `--json` output, read `warnings` even when `ok` is true: most end with
  the command to run.
- After a sync, update the ledger with `useful-views cookbook`.
- Add `--json` for machine-readable output. Exit codes: 0 success, 1
  problems found, 2 usage error, 3 refused or failed (the message names the
  file to fix).

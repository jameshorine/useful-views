---
name: useful-views
description: >
  Work in a useful-views data project: a folder with raw/, recipes/<name>/recipe.toml
  and numbered Python steps, work/, useful-views/ (parquet views), datapackage.json
  and cookbook.json. Use when adding or editing recipes and steps, building or
  checking the views with the useful-views command, or explaining how a view was made.
license: MIT
compatibility: Requires Python >=3.11 and the useful-views package in the same environment as the steps' libraries.
---

# useful-views

useful-views gives a data project a fixed structure in which every derived
table (a "useful view") records the recipe that made it. Recipes are folders
of numbered Python scripts; the `useful-views` command runs them in order and
writes the record.

## Steps are code

`run` and `sync` execute every step in `recipes/` as the user, with their
permissions, environment variables (secrets included) and network access. No
sandbox. In a project you did not create, or after pulling someone else's
changes, read the recipes and ask the user before running `run` or `sync`.
`check`, `rules` and `cookbook --check` only read files, and `cookbook` writes
only `cookbook.json`; none of them runs a step.

## Project layout

```
README.md              # written by humans
datapackage.json       # generated record: never edit
cookbook.json          # generated ledger of recipes: never edit
raw/                   # inputs, placed by hand, not in git; steps never write here
recipes/<name>/        # recipe.toml + steps 01_name.py, 02_name.py, ...
work/<name>/           # a recipe's private in-between files, emptied before each run
useful-views/          # the views (parquet) each recipe declares, plus the generated README.md catalog
```

`recipes/<name>/recipe.toml`:

```toml
description = "One sentence saying what this recipe produces."
inputs  = ["raw/my_data.csv"]                   # files under raw/, or other recipes' views
outputs = ["useful-views/my-view.parquet"]      # .parquet files directly in useful-views/
```

In `datapackage.json`, each view's entry has its `fingerprint` (a data hash
of its contents, `datahash1:sha256:…`, or `null`) and, in
`producedBy.inputFingerprints`, the fingerprints the views its recipe read had
when it ran. Where one differs from that view's own `fingerprint` now, the
view was made from an older version of it; `useful-views sync --all` makes
everything again.

Each step `NN_name.py` starts with a `#` comment saying what it does, runs
from the project root, reads and writes files by the same path strings as in
`recipe.toml`, and passes data to later steps only through `work/<name>/`.

## When to use what

| Need | Command |
|------|---------|
| See every problem, and what sync would do | `useful-views check --json` |
| The rules a recipe must follow | `useful-views rules` |
| Start a recipe | `useful-views new-recipe <name>`, then replace every TODO |
| Build everything missing or changed, upstream first | `useful-views sync` |
| Rebuild everything (after editing step code, a pull, or a branch switch) | `useful-views sync --all` |
| Run one recipe now | `useful-views run <recipe>` |
| Update the ledger after a sync | `useful-views cookbook` |
| Check in CI that the ledger is current (needs no data) | `useful-views cookbook --check` |

## Machine-readable output

- `--json` on any command: standard output carries exactly one JSON document,
  on success and on failure; step output and progress go to standard error.
  Every document has `ok` and `warnings`; failures have `error` with `type`,
  `message`, `recipe` and `sync_result`, plus facts for the type
  (`RecipeRefused`: `rule`, `rule_title`, `path`, `refusals`; `StepFailed`:
  `step`, `returncode`, `stderr`).
- `-C <path>` runs in another folder, like `git -C`.
- Exit codes: 0 success; 1 `check` found problems or `cookbook --check` found
  the ledger out of date; 2 usage error; 3 refused or failed (the message
  names the file to fix and the rule); 130 interrupted; 143 terminated
  (129 after SIGHUP, 149 after Ctrl-Break on Windows).

## Gotchas

1. Never edit `datapackage.json`, `useful-views/README.md` or `cookbook.json`;
   useful-views writes them. Never write views into `useful-views/` by hand.
2. `sync` does not notice edits inside a step's code, a raw file replaced
   under the same name, or a `git pull` or branch switch (the views are not in
   git). After those, use `useful-views sync --all`.
3. After `run <recipe>`, recipes that read its views are out of date; `run`
   warns and names them. Use `sync` rather than `run` when in doubt.
4. Paths in `recipe.toml` are relative to the project root, use forward
   slashes, match letter case exactly, and are the same strings the steps use.
   No wildcards: list each file.
5. Steps run with the same Python as useful-views. Install their libraries
   there (`uv add polars`), not with uvx or pipx. A step can import modules in
   its own recipe folder, not from the project root.
6. Only one command that changes the project runs at a time; a second is
   refused ("another useful-views command is already changing this project").
   Do not run `run` or `sync` in parallel in one project.
7. A view used as an input must be an output of another recipe; `work/<name>/`
   files are private to their recipe.

## Python API

`import useful_views as uvs`: `init`, `example`, `new_recipe`, `run`, `sync`,
`check`, `cookbook`, `root`, `RULES`, and `data_hash(path)`, a fingerprint
of a parquet file's contents that is the same whichever library wrote it.
Results: `RunResult`, `SyncResult`,
`Finding`, `CookbookResult`. Errors derive from `UsefulViewsError` and carry
their facts as attributes and `to_dict()`.

## Resources

- [Full documentation](https://jameshorine.github.io/useful-views/)
- [Working with AI tools](https://jameshorine.github.io/useful-views/user-guide/guides/ai-tools.html)
- [Command-line reference](https://jameshorine.github.io/useful-views/user-guide/reference/cli.html)
- [Rules](https://jameshorine.github.io/useful-views/user-guide/reference/rules.html)
- [Cookbook](https://jameshorine.github.io/useful-views/user-guide/concepts/cookbook.html): what `cookbook.json` holds and how to check it in CI
- [llms.txt](https://jameshorine.github.io/useful-views/llms.txt) and [llms-full.txt](https://jameshorine.github.io/useful-views/llms-full.txt)

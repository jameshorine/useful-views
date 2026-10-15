# Instructions for AI coding assistants

This is a useful-views data project (https://jameshorine.github.io/useful-views/).
Read `README.md`, `recipes/*/recipe.toml` and `useful-views/README.md` first.

- `useful-views rules` lists the rules a recipe must follow, and
  `useful-views check --json` lists every problem. Neither changes anything.
- Steps are code: `useful-views run` and `useful-views sync` execute every
  step in `recipes/` with the user's permissions and environment variables.
  Ask the user before running them on recipes you did not write.
- Never edit `datapackage.json`, `useful-views/README.md` or `cookbook.json`;
  useful-views writes them. Never write files into `raw/` or `useful-views/`
  yourself: steps write the views.
- To add a recipe: `useful-views new-recipe <name>`, then fill in its
  `recipe.toml` and steps. Paths start at the project root, use forward
  slashes, and are the same strings in `recipe.toml` and in the code.
- To build: `useful-views sync`. After editing a step's code, a `git pull` or
  a branch switch, use `useful-views sync --all`. Run one command at a time.
- After a sync, update the ledger with `useful-views cookbook`.
- Add `--json` for machine-readable output. Exit codes: 0 success, 1 problems
  found, 2 usage error, 3 refused or failed (the message names the file to fix).

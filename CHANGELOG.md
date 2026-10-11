# Changelog

All notable changes to useful-views are recorded here. Versions follow
[semantic versioning](https://semver.org/); while the version starts with
0, a minor release may change behaviour.

## 0.1.0 (unreleased)

The first release: the minimum version.

- A fixed project structure: `raw/`, `recipes/`, `work/`, `useful-views/`,
  a generated `datapackage.json` and a generated catalog,
  `useful-views/README.md`.
- `init`, `new_recipe`, `run`, `check`, `sync` and `root`, as Python
  functions and as the `useful-views` command.
- `example` downloads a ready-to-run example project from the
  useful-views-data repository, pinned to a tag and checked by SHA-256.
- `check` reports what `run` and `sync` would refuse (`refused`, with the
  rule number) and recipes changed since they last ran (`out-of-date`).
  `sync` checks every recipe before changing anything and lists every
  problem at once.
- For scripts and AI tools: `--json` on every command, `-C <path>`,
  `useful-views rules` (and `useful_views.RULES`), exit codes in `--help`,
  and errors that carry the recipe, rule, file and step (`to_dict()`).
  Progress lines go to standard error.
- Recipes are folders with a `recipe.toml` and numbered Python step files;
  every view records the recipe, inputs and steps that made it.
- `sync` makes the views match the recipe folders, like `uv sync`: it runs
  what is missing or changed, upstream first, and removes what is no longer
  declared.
- Schemas are read with pyarrow; the generated `datapackage.json` passes
  `frictionless validate`.
- A documentation site built with great-docs: getting started, concepts, a
  guide for AI tools, a generated command-line and rules reference, an API
  reference from the docstrings, and `llms.txt` / `SKILL.md` for coding
  agents.
- Tested on Python 3.11 to 3.14.

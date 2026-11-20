---
title: "Working with AI tools"
---

useful-views is built to be driven by an AI coding assistant as well as by a
person. Its files are plain, its rules are checked before anything changes,
its messages name the file to fix, and its output can be read by a program.
This page is for the assistant, or for you when you instruct one.

## Instructions in every project

`useful-views init` writes an `AGENTS.md` into each new project. It is a
short set of instructions that many coding assistants read by themselves
when they open a folder. It is yours to edit, and `init` never overwrites
it. Claude Code reads `CLAUDE.md` instead. A `CLAUDE.md` containing the line
`@AGENTS.md` makes it read the same instructions.

`run` and `sync` run the project's steps as you. Before an assistant runs
them on changes it didn't write, such as a cloned repository, a pull request
or a downloaded project, it should read the recipes and ask you. See
[Steps are code](../concepts/steps.md#steps-are-code).

## What to tell an assistant

- Read the project's `README.md`, `recipes/*/recipe.toml` and the catalog
  `useful-views/README.md` first.
- Never edit `datapackage.json`, `useful-views/README.md` or
  `cookbook.json`. useful-views writes them. Never put files in `raw/` or
  `useful-views/` by hand.
- Start with `useful-views rules` and `useful-views check --json`. Neither
  runs a step. `check` lists every problem, including every rule `run` or
  `sync` would refuse, and every recipe `sync` would run, with the reason.
- To add a recipe, run `useful-views new-recipe <name>`. Then fill in its
  `recipe.toml` and replace the stub step. [Recipes](../concepts/recipes.md)
  covers `recipe.toml`, its `[[steps]]` blocks and its paths, and
  [Steps](../concepts/steps.md) covers step files.
- To build, run `useful-views sync`. It refuses before changing anything if
  any recipe breaks a rule, and lists every problem at once.
- After editing the code of a step, run its recipe with `useful-views run`,
  then what the warnings say to run. After a `git pull`, a branch switch,
  or a raw file replaced under the same name, run `useful-views sync --all`.
  See [What to run after a change](../concepts/running.qmd#what-to-run-after-a-change).
- Run one command at a time. A second `run`, `sync` or `cookbook` started
  while another is working on the same project is refused ("another
  useful-views command is already changing this project"). Wait, then run it
  again.
- After a sync, update the ledger with `useful-views cookbook`. In CI,
  `useful-views cookbook --check` checks it without the data (see
  [Cookbook](../concepts/cookbook.md)).

## Machine-readable output

Every command takes `--json`, including `--help` and `--version`. Standard
output then carries exactly one JSON document, on success and on failure.
Everything else, including the steps' own output, goes to standard error.

Every document has `ok` and a `warnings` list. Read `warnings` even when
`ok` is true. A note there can matter, such as a recipe `check` can't
judge, and most end with the command to run.

In the example project, after a change to `recipes/pwt_panel/recipe.toml`
(messages shortened):

```sh
useful-views check --json
```

```json
{
  "ok": false,
  "findings": [
    {
      "kind": "out-of-date",
      "path": "recipes/gdp_per_capita",
      "message": "recipes/gdp_per_capita is out of date: ... Bring it up to date with: useful-views sync",
      "rule": null,
      "recipe": "gdp_per_capita",
      "reason": "upstream"
    },
    {
      "kind": "out-of-date",
      "path": "recipes/pwt_panel",
      "message": "recipes/pwt_panel is out of date: ... Bring it up to date with: useful-views sync",
      "rule": null,
      "recipe": "pwt_panel",
      "reason": "changed"
    }
  ],
  "warnings": []
}
```

[Check findings](../reference/check-findings.md) lists every `kind` and
`reason`. Then:

```sh
useful-views sync --json
```

```json
{
  "ok": true,
  "removed": [],
  "dropped": [],
  "deleted": [],
  "ran": [
    "pwt_panel",
    "gdp_per_capita"
  ],
  "reasons": {
    "pwt_panel": "changed",
    "gdp_per_capita": "upstream"
  },
  "warnings": []
}
```

`reasons` says why each recipe ran: `never-run`, `changed`,
`outputs-changed`, `views-missing`, `input-data-changed`, `upstream` or
`all`.

On failure, `ok` is false and `error` says what went wrong. Here a step of
`pwt_panel` fails (`message` and `stderr` are shortened):

```json
{
  "ok": false,
  "error": {
    "type": "StepFailed",
    "message": "Step recipes/pwt_panel/02_keep_core_variables.py failed ...",
    "recipe": "pwt_panel",
    "sync_result": null,
    "step": "recipes/pwt_panel/02_keep_core_variables.py",
    "returncode": 1,
    "stderr": "Traceback (most recent call last): ..."
  },
  "warnings": []
}
```

Every `error` has `type`, `message`, `recipe` and `sync_result`, which is
what `sync` had done before it stopped, or `null`. Some types add facts:

| Type | Adds |
|---|---|
| `RecipeRefused` | `rule`, `rule_title`, `path` and `refusals` |
| `StepFailed` | `step`, `returncode` and `stderr` (at most its last 64 KB) |
| `OutputError` | `paths` and `problems` |
| `CookbookError` | `findings` |
| `ExampleError` | `kind` |

From Python, every exception carries the same facts, as attributes and in
`to_dict()`.

With `cookbook --check --json`, `ok` is the answer. `findings` lists what
is out of step, as the same objects `check` gives: recipe folders that no
longer match their records, and recipes made from an older version of a
view they read. It is empty when the only problem is that `cookbook.json`
is missing or differs, so test `ok`. When `cookbook` (without `--check`)
refuses to write, it exits with 3, and its findings are in
`error.findings`.

The exit codes are in the
[command-line reference](../reference/cli.md#exit-codes).

`-C <path>` runs a command in another folder, like `git -C`, so an
assistant whose working directory resets between commands doesn't need
`cd`.

## For the assistant's own context

The documentation site publishes:

- an Agent Skills [`SKILL.md`](../../skill.md) on how to work in a
  useful-views project;
- [`llms.txt`](../../llms.txt), an index of the Python API, and
  [`llms-full.txt`](../../llms-full.txt), the whole documentation as plain
  text;
- the [command-line reference](../reference/cli.md), the
  [rules](../reference/rules.md) and the
  [check findings](../reference/check-findings.md).

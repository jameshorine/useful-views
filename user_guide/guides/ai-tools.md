---
title: "Working with AI tools"
---

useful-views is built to be driven by an AI coding assistant as well as by a
person: plain files, rules checked before anything changes, messages that
name the file to fix, and machine-readable output. This page is for the
assistant, or for you when you instruct one.

## Instructions in every project

`useful-views init` writes an `AGENTS.md` into each new project: a short set
of instructions that many coding assistants read by themselves when they open
a folder. It is yours to edit, and `init` never overwrites it. Claude Code
reads `CLAUDE.md` instead; a `CLAUDE.md` containing the line `@AGENTS.md`
makes it read the same instructions.

## Steps are code

`useful-views run` and `useful-views sync` execute every step in `recipes/`
as an ordinary Python program, with your permissions, your environment
variables (including any passwords or tokens in them) and your network
access. There is no sandbox. An assistant should read the recipes, and ask
you, before running `run` or `sync` in a project or on changes it did not
write: a cloned repository, a pull request, a downloaded project.

These never run a step: `check`, `rules` and `cookbook --check`, which only
read files, and `cookbook`, which writes only `cookbook.json`.

## What to tell an assistant

- Read the project's `README.md`, `recipes/*/recipe.toml` and the catalog
  `useful-views/README.md` first. Never edit `datapackage.json`,
  `useful-views/README.md` or `cookbook.json`; useful-views writes them. Never
  put files in `raw/` or `useful-views/` by hand.
- Start with `useful-views rules` and `useful-views check --json`. `check`
  lists every problem, including every rule `run` or `sync` would refuse, and
  every recipe `sync` would run, with the reason.
- To add a recipe: `useful-views new-recipe <name>`, then fill in its
  `recipe.toml` and replace the stub step. Paths are the same strings in
  `recipe.toml` and in the step's code, relative to the project root, with
  forward slashes, and with no wildcards.
- To build: `useful-views sync`. It refuses before changing anything if any
  recipe breaks a rule, and lists every problem at once.
- After editing the code inside a step, after a `git pull` or a branch
  switch, or after replacing a raw file under the same name, `sync` alone
  does not notice: use `useful-views sync --all`.
- After `run <recipe>`, the recipes that read its views are out of date; the
  warning names them. Prefer `sync`.
- Run one command at a time. A second `run`, `sync` or `cookbook` started
  while another is working on the same project is refused ("another
  useful-views command is already changing this project"); wait, then run
  it again.
- After a sync, update the ledger: `useful-views cookbook`. In CI,
  `useful-views cookbook --check` checks it without needing the data; see
  [Cookbook](../concepts/cookbook.md).

## Machine-readable output

Every command takes `--json`, including `--help` and `--version`. Standard
output then carries exactly one JSON document, on success and on failure;
everything else, including the steps' own output, goes to standard error.
Every document has `ok` and a `warnings` list.

```sh
useful-views check --json
# {"ok": false, "findings": [{"kind": "out-of-date", "path": "recipes/b", "message": ...,
#   "rule": null, "recipe": "b", "reason": "upstream"}], "warnings": []}

useful-views sync --json
# {"ok": true, "removed": [], "dropped": [], "deleted": [], "ran": ["a", "b"],
#  "reasons": {"a": "changed", "b": "upstream"}, "warnings": []}

useful-views run pwt_panel --json
# on failure:
# {"ok": false, "error": {"type": "StepFailed", "message": ..., "recipe": "pwt_panel",
#   "sync_result": null, "step": ..., "returncode": 1, "stderr": ...}, "warnings": []}
```

Every `error` has `type`, `message`, `recipe` and `sync_result` (what `sync`
had done before it stopped, or `null`), plus facts for its type:
`RecipeRefused` has `rule`, `rule_title`, `path` and `refusals`;
`StepFailed` has `step`, `returncode` and `stderr` (at most its last 64 KB);
`OutputError` has `paths` and `problems`; `CookbookError` has `findings`;
`ExampleError` has `kind`.

| Exit code | Meaning |
|---|---|
| 0 | success |
| 1 | `check` found problems, or `cookbook --check` found the cookbook out of date |
| 2 | usage error: unknown command or option, or a bad `-C` path |
| 3 | refused or failed: the message says what to fix, and in which file |
| 130 | interrupted (Ctrl-C) |
| 143 | terminated (SIGTERM, from a CI cancel or a tool's time limit); 129 for SIGHUP, 149 for Ctrl-Break on Windows |

`-C <path>` runs a command in another folder, like `git -C`, so an assistant
whose working directory resets between commands does not need `cd`.

From Python, every exception carries the same facts as attributes and
`to_dict()`.

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

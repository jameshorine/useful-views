---
title: "Working with AI tools"
guide-section: Guides
---

useful-views is built to be driven by an AI coding assistant as well as by a
person: plain files, rules checked before anything changes, messages that
name the file to fix, and machine-readable output. This page is for the
assistant, or for you when you instruct one.

## What to tell an assistant

- Read the project's `README.md`, `recipes/*/recipe.toml` and the catalog
  `useful-views/README.md` first. Never edit `datapackage.json` or
  `useful-views/README.md`; useful-views writes them.
- Start with `useful-views check --json`. It lists every problem, including
  every rule `run` or `sync` would refuse, and every recipe `sync` would run.
- To add a recipe: `useful-views new-recipe <name>`, then fill in its
  `recipe.toml` and replace the stub step. Paths are the same strings in
  `recipe.toml` and in the step's code.
- To build: `useful-views sync`. It refuses before changing anything if any
  recipe breaks a rule, and lists every problem at once.
- After editing the code inside a step, `sync` alone does not notice: use
  `useful-views sync --all`, or `run` that recipe and those downstream.
- `useful-views rules` lists the rules; every refusal names the rule it
  broke.

## Machine-readable output

Every command takes `--json`. Standard output then carries exactly one JSON
document, on success and on failure; everything else goes to standard error.

```sh
useful-views check --json
# {"ok": false, "findings": [{"kind": ..., "path": ..., "rule": ...}]}

useful-views sync --json
# {"ok": true, "ran": [...], "removed": [...], ...}

useful-views run pwt_panel --json
# on failure:
# {"ok": false, "error": {"type": "StepFailed", "step": ..., "stderr": ...}}
```

`-C <path>` runs a command in another folder, like `git -C`, so an assistant
whose working directory resets between commands does not need `cd`.

From Python, every exception carries the same facts as attributes and
`to_dict()`: `RecipeRefused.rule`, `.path` and `.recipe`; `StepFailed.step`
and `.stderr`; `OutputError.problems`; and, when `sync` stopped partway,
`sync_result`.

## For the assistant's own context

The documentation site publishes `llms.txt` and `llms-full.txt` (the whole
documentation as plain text) and an Agent Skills `SKILL.md` describing how
to use the package.

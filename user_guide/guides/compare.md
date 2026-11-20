---
title: "How useful-views compares"
---

useful-views gives a data project a fixed structure. It isn't a general
workflow engine. Every derived table lives in `useful-views/` and records
the recipe, inputs and steps that made it.

Recipes are plain folders of numbered Python files, so you can understand a
project by reading it. There's no rule language and no configuration layer.
There is no official pipeline definition, but a workflow definition is
provided by each recipe's `recipe.toml`: its inputs, its steps in the order
they run with what each does, and its outputs. The order of the recipes
follows from what each one reads and makes.

The same structure makes a project easy for AI coding tools to work in.
They get an `AGENTS.md`, `--json` output, and numbered rules to check
against. See [Working with AI tools](ai-tools.md).

## Other tools

- **Snakemake** is a full workflow engine, with its own rule language,
  wildcards, and cluster and cloud execution. Choose it for large or highly
  parallel pipelines. Choose useful-views for a small, readable project
  where provenance comes from the layout.
- **Kedro** is a framework for production data pipelines, with a data
  catalog, configuration environments and a project template built for
  teams. useful-views is a lighter fit for one researcher or a small lab.
- **DVC** versions data and pipelines alongside git, and syncs them to
  remote storage. useful-views doesn't version or store data. It records how
  each derived table was made, and can sit alongside DVC or Git LFS.
- **Frictionless Data Packages** are a standard for describing datasets,
  not a tool for making them. useful-views writes a standard
  `datapackage.json`, so its records work with Frictionless tools.

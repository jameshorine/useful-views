---
title: "Projects"
guide-section: Concepts
---

A useful-views project is a folder with a fixed structure. Every project
looks the same, so a teammate, a reviewer or an AI tool knows where
everything is.

```text
my-data-project/
├── README.md          # written by you: what the project is, where the data came from
├── datapackage.json   # generated: what each recipe made from what
├── .gitignore         # written once by init
├── raw/               # raw inputs, placed by hand; never written by a recipe
├── recipes/           # one folder per recipe, written by you
├── work/              # in-between files, one folder per recipe, emptied before each run
└── useful-views/      # the views the recipes declare, plus the generated catalog README.md
```

Create one with `useful-views init my-data-project`, or start from a
ready-made example with `useful-views example pwt`.

## The four kinds of data

| Kind | Where | Made by | In git |
|---|---|---|---|
| Raw input | `raw/` (subfolders allowed) | You, by hand | No |
| Work file | `work/<recipe>/` | A recipe's steps, for its own later steps | No |
| Useful view | `useful-views/`, flat | A recipe, as a declared output | No (parquet); the catalog yes |
| Record | `datapackage.json`, `useful-views/README.md` | useful-views, after each successful run | Yes |

- **Raw inputs** are the data you start from. Nothing a recipe does writes to
  `raw/`. Because `raw/` is not committed, say in the project's `README.md`
  where each file came from.
- **Work files** pass data from one step to the next within a recipe. No
  other recipe may read them, and `run` empties `work/<recipe>/` before each
  run, so a leftover never passes for a fresh result.
- **Useful views** are the derived tables you actually use. Each is a
  `.parquet` file made by exactly one recipe, and another recipe may read it
  as an input.
- **Records** say which recipe made each view, from which inputs, with which
  steps. See [Records](records.md).

## Who writes what

You write the project `README.md` and everything in `recipes/`; `init` and
`new-recipe` only start them from templates and never touch them again.
useful-views writes `datapackage.json` and `useful-views/README.md`; never
edit those by hand. `init` writes `.gitignore` once, if there is none; after
that it is yours.

## What to commit

Commit `README.md`, `recipes/`, `datapackage.json`, `.gitignore`,
`raw/.gitkeep` and `useful-views/README.md`. The generated `.gitignore`
keeps the rest out:

```text
raw/*
!raw/.gitkeep
work/
useful-views/*.parquet
```

A teammate who clones the project places the raw files as your `README.md`
describes, then runs `useful-views sync`, which makes every view again.

## Finding the project from a subfolder

Every command and function finds the project by walking up from the current
folder to `datapackage.json`, so they work from a notebook in `notebooks/`.
From anywhere else, name it: `useful-views -C path/to/project check`, or
`root=` in Python.

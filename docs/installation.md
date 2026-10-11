---
title: "Installation"
guide-section: Get started
---

useful-views needs Python 3.11 or newer. Install it into the same Python
environment as the libraries your recipe steps use, such as polars or pandas:
steps run with the Python that runs useful-views.

## With uv

```sh
uv add useful-views
```

`uv add` records the dependency in your project's `pyproject.toml`. Add your
steps' libraries the same way, for example `uv add polars`. Run commands with
`uv run useful-views ...`, or activate the environment first.

## With pip

```sh
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install useful-views
```

## In a notebook

The notebook must run in the same environment. Install Jupyter into it
(`uv add jupyterlab` or `pip install jupyterlab`) and start it from your
project folder, or in VS Code choose the environment's `.venv` as the
notebook's kernel. If `import useful_views` fails in a notebook, the notebook
is running a different Python.

## Check the installation

```sh
useful-views --version
```

If the `useful-views` command is not found, `python -m useful_views
--version` runs the same program with the Python you name.

## Next

The [quickstart](quickstart.md) builds a ready-made example project and adds
a recipe of your own.

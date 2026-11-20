---
title: "Installation"
---

useful-views needs Python 3.11 or newer. Install it into the same environment
as the libraries your steps use, such as polars or pandas, because steps run
with the Python that runs useful-views.

## With uv

```sh
uv init --bare             # once, if the folder has no pyproject.toml
uv add useful-views polars
```

[uv](https://docs.astral.sh/uv/) fetches a suitable Python for you, so it is
the easiest start on a new computer. `uv add` records each library in
`pyproject.toml` and `uv.lock`. Add your steps' other libraries the same way,
for example `uv add pandas`.

Put `uv run` in front of each command, as in `uv run useful-views check`. Or
activate the environment first: `source .venv/bin/activate`, or
`.venv\Scripts\activate` on Windows.

Without a `pyproject.toml`, make a plain environment:

```sh
uv venv
uv pip install useful-views polars
```

## With pip

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install useful-views polars
```

On Windows, in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install useful-views polars
```

First check that `python3 --version` (on Windows, `py --version`) says 3.11
or newer. If it doesn't, install a newer Python from
[python.org](https://www.python.org/downloads/), or use uv.

If PowerShell says that running scripts is disabled, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. Or use the Command
Prompt, where the second line is `.venv\Scripts\activate.bat`.

## With conda

useful-views isn't on conda-forge yet. Make a conda environment with your
steps' libraries, then install useful-views into it with pip:

```sh
conda create -n my-project python=3.12 polars pyarrow
conda activate my-project
pip install useful-views
```

## Not as a separate tool

Don't run useful-views with `uvx`, `uv tool install` or `pipx`. They give it
an environment of its own, where your steps can't find your project's
libraries.

## In a notebook

The notebook must run in the same environment:

- Install Jupyter into it, with `uv add jupyterlab` or `pip install
  jupyterlab`, and start it from your project folder.
- In VS Code, choose the environment's `.venv` as the notebook's kernel.

If `import useful_views` fails in a notebook, the notebook is running a
different Python. [From Python](guides/python.qmd) walks through a notebook
session.

## Record the environment

Commit the list of your steps' libraries with the project. A co-author or a
reviewer can then rebuild the same environment.

| Installed with | Commit | A co-author runs |
|---|---|---|
| uv | `pyproject.toml` and `uv.lock` | `uv sync` |
| pip | `requirements.txt`, from `pip freeze > requirements.txt` | `pip install -r requirements.txt` |
| conda | `environment.yml`, from `conda env export --from-history > environment.yml` | `conda env create -f environment.yml` |

`--from-history` lists only what you installed with conda. Add useful-views,
and any other library you installed with pip, under `pip:` yourself:

```yaml
dependencies:
  - python=3.12
  - polars
  - pyarrow
  - pip
  - pip:
      - useful-views
```

## Check the installation

```sh
useful-views --version
```

If the `useful-views` command isn't found, `python -m useful_views --version`
runs the same program with the Python you name.

Next, [Get started](quickstart.md) walks through an example project.

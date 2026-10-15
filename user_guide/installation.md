---
title: "Installation"
---

useful-views needs Python 3.11 or newer. Install it into the same Python
environment as the libraries your recipe steps use, such as polars or pandas:
steps run with the Python that runs useful-views.

## With uv (recommended)

[uv](https://docs.astral.sh/uv/) fetches a suitable Python for you, so it is
the easiest start on a new computer. In your project folder:

```sh
uv init --bare             # once: creates a pyproject.toml for this folder
uv add useful-views polars
```

`uv add` records each library in `pyproject.toml` and `uv.lock`. Add your
steps' other libraries the same way, for example `uv add pandas`. Run
commands with `uv run useful-views ...`, or activate the environment first
(`source .venv/bin/activate`; on Windows `.venv\Scripts\activate`).

Without a `pyproject.toml`, use a plain environment instead:

```sh
uv venv
uv pip install useful-views polars
```

## With pip

Check your Python first: `python3 --version` (on Windows, `py --version`)
must say 3.11 or newer. If it does not, install a newer Python from
[python.org](https://www.python.org/downloads/), or use uv.

macOS and Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install useful-views polars
```

Windows (PowerShell):

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install useful-views polars
```

If PowerShell says that running scripts is disabled, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use the
Command Prompt, where the second line is `.venv\Scripts\activate.bat`.

## With conda

useful-views is not on conda-forge yet. Make a conda environment with your
steps' libraries, then install useful-views into it with pip:

```sh
conda create -n my-project python=3.12 polars pyarrow
conda activate my-project
pip install useful-views
```

## Not as a separate tool

Do not run useful-views with `uvx`, `uv tool install` or `pipx`. Those give
it an environment of its own, and the steps, which run with the same Python,
would not find your project's libraries. Install it into the project's
environment, as above.

## In a notebook

The notebook must run in the same environment. Install Jupyter into it
(`uv add jupyterlab` or `pip install jupyterlab`) and start it from your
project folder, or in VS Code choose the environment's `.venv` as the
notebook's kernel. If `import useful_views` fails in a notebook, the notebook
is running a different Python.

## Record the environment

Commit the list of libraries your steps use, next to the project, so a
co-author or a reviewer can rebuild the same environment:

| Installed with | Commit | A co-author runs |
|---|---|---|
| uv, in a project | `pyproject.toml` and `uv.lock` | `uv sync` |
| pip | `requirements.txt`, from `pip freeze > requirements.txt` | `pip install -r requirements.txt` |
| conda | `environment.yml`, from `conda env export --from-history > environment.yml`, then add useful-views by hand (see below) | `conda env create -f environment.yml` |

`--from-history` lists only what you installed with conda, not what pip
installed. Add useful-views (and any other pip-installed library) to the
file yourself, under `pip`:

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

If the `useful-views` command is not found, `python -m useful_views
--version` runs the same program with the Python you name.

## Next

The [quickstart](quickstart.md) builds a ready-made example project and adds
a recipe of your own.

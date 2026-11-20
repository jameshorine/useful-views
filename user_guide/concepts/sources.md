---
title: "Sources"
---

`raw/` is not in git, so a project has to say where each raw file comes
from. `sources.toml`, at the project root, says it in a form useful-views can
read: for each data source, the raw files it covers, how to get it, its
license, how to cite it, and whether its files are shared with the project.
useful-views copies these details into `datapackage.json`, and the catalog
and the cookbook show them.

A project does not need a `sources.toml`. Once it has one, useful-views
also notes each raw file that no source covers.

## sources.toml

You write it, one `[[sources]]` block per source. One source can cover
several raw files.

```toml
[[sources]]
title = "Penn World Table 11.0 (extract for Brazil, Germany, Spain and the United States)"
files = ["raw/pwt110_bra_deu_esp_usa.csv"]
url = "https://www.rug.nl/ggdc/productivity/pwt/"
doi = "10.34894/FABVLR"
access = """
All variables and all years (1950 to 2023) for the four countries, exported
from the PWT online data access tool and unchanged apart from its name."""
cost = "Free"
obtained = 2026-10-09
license = "CC-BY-4.0"
license_url = "https://creativecommons.org/licenses/by/4.0/"
citation = """
Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "The Next
Generation of the Penn World Table." American Economic Review, 105(10),
3150-3182. https://doi.org/10.1257/aer.20130954"""
shared = true
```

| Key | | What it holds |
|---|---|---|
| `title` | required | What the data is, as its publisher names it. |
| `files` | required | The raw files it covers, written as in `recipe.toml`: from the project root, under `raw/`. Each raw file belongs to one source only. |
| `url` | | Where to find it. |
| `doi` | | Its DOI, such as `10.34894/FABVLR`. |
| `access` | | How to get it: the steps, any registration or application, and any limits on access. |
| `cost` | | What it costs to get. |
| `time` | | How long it takes to get (an application can take months). |
| `obtained` | | When you got it, as a date: `obtained = 2026-10-09`. |
| `license` | | Its license: an identifier such as `CC-BY-4.0` or `ODC-BY-1.0`, or its name. |
| `license_url` | | Where its license is. |
| `citation` | | How to cite it. |
| `shared` | | `true` if its files are shared with the project (in its replication package), `false` if not. |
| `reason` | | Why not, when `shared = false`: confidential, licensed, too large, ... |

Every key but `title` and `files` is optional, but the more a source says,
the easier it is to find again. Spaces and line breaks in a value are joined
into one line, so a long one can be written over several lines in triple
quotes, `"""..."""`. Text with double quotes in it needs triple quotes too. A
backslash starts an escape sequence: write it as `\\`, or put the text in
triple single quotes, `'''...'''`, which keep it as written. Other keys, at
the top or in a `[[sources]]` block, are ignored with a warning, so check the
spelling. Save the file as UTF-8, and commit it.

## Where the details go

In `datapackage.json`, each raw file a recipe read gets its source's details,
in the Data Package's own fields: `sources` (its `title`, and its `url` as
`path`, or a link from its DOI), with the other details beside them, and
`licenses`. A license that is an identifier is written as its `name`, any
other as its `title`, with `license_url` as its `path`. A license that is
neither an identifier nor has a URL stays in the source, as `license`, since
the Data Package standard requires one or the other.

```json
{
  "name": "pwt110_bra_deu_esp_usa",
  "path": "raw/pwt110_bra_deu_esp_usa.csv",
  ...
  "sources": [
    {
      "title": "Penn World Table 11.0 (extract for Brazil, Germany, Spain and the United States)",
      "path": "https://www.rug.nl/ggdc/productivity/pwt/",
      "doi": "10.34894/FABVLR",
      ...
      "shared": true
    }
  ],
  "licenses": [{"name": "CC-BY-4.0", "path": "https://creativecommons.org/licenses/by/4.0/"}],
  "schema": {...}
}
```

The catalog, `useful-views/README.md`, ends with a section on the raw data
sources, and names any raw file that has none. The cookbook lists the
sources, each with its files. A project without a `sources.toml` gets
neither.

## Keeping them in step

The details are metadata: they say where the data came from, not what it
holds. So changing `sources.toml` never makes a recipe out of date. Until
the next sync, `check` reports `stale-sources`, and `useful-views sync`
writes the new details into `datapackage.json` and the catalog without
running any recipe. `cookbook --check` reports it too, with no data needed,
so a CI job catches a `sources.toml` committed without a sync. Delete
`sources.toml`, and the next sync removes the details.

## Raw files with no source

Once a project has a `sources.toml`, `check` and `sync` note each raw file
that a recipe reads and no source lists, and `run` notes the recipe's own.
It is a note, not a finding: `check` still succeeds. A project with no
`sources.toml` gets no note.

## When sources.toml cannot be read

If it is not valid TOML, or a block lacks its `title` or `files`, lists a
path outside `raw/`, or gives a raw file to two sources, `check` reports
`unreadable-sources`, listing every problem. `run` and `sync` still run,
and warn: the details already in `datapackage.json` are kept as they were
recorded until you fix the file.

## Sources and DCAS

Journals that follow the [Data and Code Availability
Standard](https://datacodestandard.org/) (DCAS) ask a replication package to
say how to get each data source, at what cost and in what time (rule 1),
whether its raw data is shared (rules 2 and 16), how to cite it (rule 6) and
under what license (rule 15). `sources.toml` records these for the raw data
of a useful-views project. Generating the README sections a data editor
looks for is planned; see [issue
#22](https://github.com/jameshorine/useful-views/issues/22).

---
title: "Sources"
---

`raw/` isn't in git, so a project has to say where each raw file comes from.
`sources.toml`, at the project root, says it in a form useful-views can
read. A project doesn't need one.

This is the example project's `sources.toml`, with its `access` shortened:

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

You write one `[[sources]]` block for each source. One source can cover
several raw files. useful-views copies the details into `datapackage.json`,
and the catalog and the cookbook show them.

## Keys

| Key | | What it holds |
|---|---|---|
| `title` | required | What the data is, as its publisher names it. |
| `files` | required | The raw files it covers, written as in `recipe.toml`: from the project root, under `raw/`. Each raw file belongs to one source only. |
| `url` | | Where to find it. |
| `doi` | | Its DOI, such as `10.34894/FABVLR`. |
| `access` | | How to get it: the steps, any registration or application, and any limits on access. |
| `cost` | | What it costs to get. |
| `time` | | How long it takes to get. An application can take months. |
| `obtained` | | When you got it, as a date: `obtained = 2026-10-09`. |
| `license` | | Its license: an identifier such as `CC-BY-4.0` or `ODC-BY-1.0`, or its name. |
| `license_url` | | Where its license is. |
| `citation` | | How to cite it. |
| `shared` | | `true` if its files are shared with the project, in its replication package. `false` if not. |
| `reason` | | Why not, when `shared = false`: confidential, licensed, too large, ... |

Every key but `title` and `files` is optional. The more a source says, the
easier it is to find again. Long values and quotes work as in `recipe.toml`
(see [Quoting](recipes.md#quoting)). Other keys are ignored with a warning,
so check the spelling. Save the file as UTF-8, and commit it.

## Update sources

Edit `sources.toml`, then sync. No recipe runs, because the data hasn't
changed.

```sh
useful-views check   # reports stale-sources
useful-views sync    # writes the new details
```

`cookbook --check` reports the same change without the data, so CI catches a
`sources.toml` committed without a sync. See [Cookbook](cookbook.md).

Delete `sources.toml`, and the next sync removes the details.

## Where the details go

In `datapackage.json`, each raw file a recipe read gets its source's details,
in the Data Package's own fields:

- `sources`: the `title`, and the `url` as `path` (or a link made from the
  DOI), with the other details beside them;
- `licenses`: a license that is an identifier as `name`, any other as
  `title`, with `license_url` as `path`.

A license with neither an identifier nor a URL stays in the source, as
`license`, since the Data Package standard requires one or the other.

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

## Raw files with no source

Once a project has a `sources.toml`, `check` and `sync` note each raw file
that a recipe reads and no source lists. `run` notes the recipe's own. These
are notes, which don't make `check` fail. A project with no `sources.toml`
gets no note.

## When sources.toml can't be read

`check` reports `unreadable-sources`, and lists every problem, when:

- the file isn't valid TOML;
- a block lacks its `title` or `files`;
- a block lists a path outside `raw/`;
- two sources list the same raw file.

`run` and `sync` still run, and warn. The details already in
`datapackage.json` stay as they were recorded until you fix the file.

## Sources and DCAS

Journals that follow the [Data and Code Availability
Standard](https://datacodestandard.org/) (DCAS) ask a replication package to
say, for each data source:

- how to get it, at what cost and in what time (rule 1);
- whether its raw data is shared (rules 2 and 16);
- how to cite it (rule 6);
- under what license (rule 15).

`sources.toml` records these for the raw data of a useful-views project.
Writing the README sections a data editor looks for is
[issue #22](https://github.com/jameshorine/useful-views/issues/22).
